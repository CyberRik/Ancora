"""Temporal activities that dispatch compute to a backend (Ray / local).

Two execution models, per RFC-0001a §6:

* **Model A — inline** (:func:`ray_compute`): submit, then poll the backend while
  emitting Temporal heartbeats. Each heartbeat carries the latest checkpoint, so a
  crash + retry resumes from the last batch (AN-029). Cancellation of the run
  surfaces as ``CancelledError``; we cooperatively cancel the backend task
  (``ray.cancel`` / stop-event) so no compute is orphaned (AN-030). Good for short
  work where holding the dispatcher slot is cheap.

* **Model B — async completion** (:func:`ray_compute_async`): submit, hand the
  task off to a detached completer, then ``raise_complete_async()`` to *free the
  dispatcher slot immediately* (AN-028). When the backend finishes, the completer
  resolves the activity out-of-band via ``get_async_activity_handle``. A worker of
  concurrency 1 can therefore have many long activities in flight at once, and the
  worker holding the slot can die without failing the work.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any

from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError

from ancora_common.resources import ResourceSpec
from ancora_activity_worker.ray_bridge import Backend, LiveProgress, TaskHandle
from ancora_activity_worker.runtime import (
    get_backend,
    get_completion_client,
    get_node_recorder,
)
from ancora_activity_worker.tasks import ComputeRequest, ComputeResult, batched_compute

logger = logging.getLogger("ancora.runtime.activities")

# How often the inline driver samples progress and heartbeats. Kept well below a
# typical batch time so intermediate checkpoints are captured before a crash.
_HEARTBEAT_INTERVAL = 0.02

# Keep strong references to detached completion tasks so they aren't GC'd mid-flight.
_PENDING_COMPLETIONS: set[asyncio.Task[None]] = set()


def _resources(req: ComputeRequest) -> ResourceSpec:
    return ResourceSpec(
        num_cpus=req.num_cpus,
        num_gpus=req.num_gpus,
        accelerator_type=req.accelerator_type,
    )


def _resume_point(info: activity.Info) -> tuple[int, int]:
    """Read (start_from, acc) from the last heartbeat checkpoint, if any."""
    if info.heartbeat_details:
        cp = info.heartbeat_details[0]
        if isinstance(cp, dict):
            return int(cp.get("batch", 0)), int(cp.get("acc", 0))
    return 0, 0


def _bind(req: ComputeRequest, start_from: int, acc: int) -> Any:
    return functools.partial(
        batched_compute,
        label=req.label,
        total_batches=req.batches,
        batch_seconds=req.batch_seconds,
        start_from=start_from,
        acc=acc,
    )


def _node_meta(req: ComputeRequest, backend: Backend, task: TaskHandle, model: str) -> dict[str, Any]:
    info = activity.info()
    return {
        "temporal_wf_id": info.workflow_id,
        "node_name": req.label,
        "capability": _resources(req).capability.value,
        "backend": backend.name,
        "ray_task_id": task.task_id,
        "attempt": info.attempt,
        "model": model,
    }


@activity.defn(name="ray_compute")
async def ray_compute(req: ComputeRequest) -> ComputeResult:
    """Model A: dispatch to the backend, heartbeat progress, resume on retry."""
    backend = get_backend()
    recorder = get_node_recorder()
    start_from, acc = _resume_point(activity.info())

    progress = LiveProgress()
    task = backend.submit(_bind(req, start_from, acc), resources=_resources(req), progress=progress)
    await recorder.record_start(_node_meta(req, backend, task, "inline"))

    try:
        while not task.done():
            latest = progress.latest()
            if latest is not None:
                activity.heartbeat(latest)
            if activity.is_cancelled():
                task.cancel()
                raise asyncio.CancelledError
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
        # Flush the final checkpoint so a failure right at the end still resumes.
        final = progress.latest()
        if final is not None:
            activity.heartbeat(final)
        raw = await asyncio.to_thread(task.result)
    except asyncio.CancelledError:
        task.cancel()
        meta = _node_meta(req, backend, task, "inline")
        meta["status"] = "Cancelled"
        await recorder.record_finish(meta)
        raise

    result = ComputeResult(backend=backend.name, resumed_from=start_from, **raw)
    meta = _node_meta(req, backend, task, "inline")
    meta["status"] = "Completed"
    await recorder.record_finish(meta)
    return result


@activity.defn(name="ray_compute_async")
async def ray_compute_async(req: ComputeRequest) -> ComputeResult:
    """Model B: hand off to a detached completer and free the dispatcher slot."""
    backend = get_backend()
    recorder = get_node_recorder()
    client = get_completion_client()
    task_token = activity.info().task_token

    progress = LiveProgress()
    task = backend.submit(_bind(req, 0, 0), resources=_resources(req), progress=progress)
    await recorder.record_start(_node_meta(req, backend, task, "async"))

    loop = asyncio.get_running_loop()
    completion = loop.create_task(
        _complete_async(client, task_token, task, backend, _node_meta(req, backend, task, "async"))
    )
    _PENDING_COMPLETIONS.add(completion)
    completion.add_done_callback(_PENDING_COMPLETIONS.discard)

    # Free the slot NOW; the completer resolves this activity when compute finishes.
    activity.raise_complete_async()


async def _complete_async(
    client: Client,
    task_token: bytes,
    task: TaskHandle,
    backend: Backend,
    meta: dict[str, Any],
) -> None:
    """Await the detached compute and resolve the activity out-of-band."""
    handle = client.get_async_activity_handle(task_token=task_token)
    try:
        raw = await asyncio.to_thread(task.result)
        result = ComputeResult(backend=backend.name, resumed_from=0, **raw)
        await handle.complete(result)
        meta["status"] = "Completed"
        await get_node_recorder().record_finish(meta)
    except Exception as exc:  # noqa: BLE001 — surface any failure to Temporal
        logger.warning("async compute failed for %s: %s", meta.get("node_name"), exc)
        try:
            await handle.fail(ApplicationError("async compute failed", str(exc)))
        except Exception as report_exc:  # noqa: BLE001
            logger.error("failed to report async failure: %s", report_exc)
        meta["status"] = "Failed"
        await get_node_recorder().record_finish(meta)


# Registries consumed by the worker.
ACTIVITIES = [ray_compute, ray_compute_async]
