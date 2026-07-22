"""Worker-kill scenarios 1-3 (AN-037, RFC-0001a §8).

Split roles across two task queues, exactly as production does:
  * a **workflow** worker on the orchestration queue, and
  * an **activity** worker on the ``cpu`` capability queue.

Scenario 1 — kill the *workflow* worker mid-run → a fresh one resumes from history.
Scenario 2 — kill the *activity* worker during an inline compute → Temporal retries
             on a new activity worker, which resumes from the last checkpoint
             (recovery + no double full-compute).
Scenario 3 — kill the *workflow* worker while an activity is async-handed-off →
             the activity worker completes it out-of-band regardless, and a fresh
             workflow worker observes exactly one completion (async work unaffected,
             dup-safe).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from temporalio.common import RetryPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ancora import Workflow, workflow
from ancora_activity_worker import runtime
from ancora_activity_worker.activities import ACTIVITIES, ray_compute, ray_compute_async
from ancora_activity_worker.tasks import ComputeRequest, ComputeResult
from ancora_common.resources import Capability, queue_for

pytestmark = pytest.mark.temporal

_WF_QUEUE = "tq-wf-kill"
_CPU_QUEUE = queue_for(Capability.CPU)


@pytest_asyncio.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    """A *real* (wall-clock) dev server — overrides the conftest time-skipping env.

    These scenarios kill workers mid-flight and depend on real heartbeat timeouts
    and real async completion. Time-skipping has no worker to anchor the clock once
    one is killed, so it can skip past a running activity's timeout; a real server
    keeps time honest. (The dev-server binary is downloaded + cached on first run.)
    """
    environment = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter)
    try:
        yield environment
    finally:
        await environment.shutdown()


@workflow.defn
class InlinePipeline(Workflow):
    @workflow.run
    async def run(self, req: ComputeRequest) -> ComputeResult:
        return await self.call(
            ray_compute,
            req,
            task_queue=_CPU_QUEUE,
            start_to_close_timeout=timedelta(seconds=60),
            heartbeat_timeout=timedelta(seconds=8),
            retry=RetryPolicy(maximum_attempts=5, initial_interval=timedelta(milliseconds=100)),
        )


@workflow.defn
class AsyncPipeline(Workflow):
    @workflow.run
    async def run(self, req: ComputeRequest) -> ComputeResult:
        return await self.call(
            ray_compute_async,
            req,
            task_queue=_CPU_QUEUE,
            start_to_close_timeout=timedelta(seconds=300),
        )


class CountingRecorder:
    def __init__(self) -> None:
        self.starts: list[str] = []
        self.finishes: list[str] = []

    async def record_start(self, meta: dict[str, Any]) -> None:
        self.starts.append(meta["temporal_wf_id"])

    async def record_finish(self, meta: dict[str, Any]) -> None:
        if meta.get("status") == "Completed":
            self.finishes.append(meta["temporal_wf_id"])


def _wf_worker(env: WorkflowEnvironment, wf: type) -> Worker:
    return Worker(env.client, task_queue=_WF_QUEUE, workflows=[wf])


def _activity_worker(env: WorkflowEnvironment, max_concurrent: int = 50) -> Worker:
    return Worker(
        env.client,
        task_queue=_CPU_QUEUE,
        activities=ACTIVITIES,
        max_concurrent_activities=max_concurrent,
    )


# --------------------------------------------------------------------------- #
# Scenario 1 — kill the workflow worker
# --------------------------------------------------------------------------- #
async def test_scenario1_kill_workflow_worker(env: WorkflowEnvironment) -> None:
    req = ComputeRequest(label="s1", batches=4, batch_seconds=0.01)
    async with _activity_worker(env):  # activity worker stays up throughout
        wf_a = _wf_worker(env, InlinePipeline)
        await wf_a.__aenter__()
        handle = await env.client.start_workflow(
            InlinePipeline.run, req, id="wf-s1", task_queue=_WF_QUEUE
        )
        # Kill the workflow worker almost immediately (before it can finish).
        await wf_a.__aexit__(None, None, None)

        # A fresh workflow worker resumes from history and completes.
        async with _wf_worker(env, InlinePipeline):
            result = await handle.result()
    assert result.checksum == 7 * (1 + 2 + 3 + 4)


# --------------------------------------------------------------------------- #
# Scenario 2 — kill the activity worker during an inline compute
# --------------------------------------------------------------------------- #
async def _wait(cond, tries: int = 400) -> None:
    for _ in range(tries):
        if cond():
            return
        await asyncio.sleep(0.02)
    pytest.fail("condition not met in time")


async def test_scenario2_kill_activity_worker_inline(env: WorkflowEnvironment) -> None:
    recorder = CountingRecorder()
    runtime.set_node_recorder(recorder)

    # Long enough that we can kill the worker while a compute is in flight.
    req = ComputeRequest(label="s2", batches=60, batch_seconds=0.02)

    async with _wf_worker(env, InlinePipeline):  # workflow worker stays up
        act_a = _activity_worker(env)
        await act_a.__aenter__()
        handle = await env.client.start_workflow(
            InlinePipeline.run, req, id="wf-s2", task_queue=_WF_QUEUE
        )
        # Wait until the activity worker has actually started computing.
        await _wait(lambda: len(recorder.starts) >= 1)
        await asyncio.sleep(0.3)  # let it advance a few checkpoints
        # Kill the activity worker mid-compute.
        await act_a.__aexit__(None, None, None)

        # Fresh activity worker: Temporal retries the activity, which resumes.
        async with _activity_worker(env):
            result = await handle.result()

    assert result.checksum == 7 * (60 * 61 // 2)  # full, correct compute
    assert result.resumed_from > 0, "retry did not resume from a checkpoint"
    assert len(recorder.starts) >= 2, "activity should have been (re)started on a 2nd worker"
    assert recorder.finishes.count("wf-s2") == 1, "exactly one successful completion"


# --------------------------------------------------------------------------- #
# Scenario 3 — kill the workflow worker while work is async-handed-off
# --------------------------------------------------------------------------- #
async def test_scenario3_async_handoff_survives_wf_worker_kill(env: WorkflowEnvironment) -> None:
    recorder = CountingRecorder()
    runtime.set_node_recorder(recorder)
    runtime.set_completion_client(env.client)

    req = ComputeRequest(label="s3", batches=10, batch_seconds=0.04)

    async with _activity_worker(env):  # activity worker (+ detached completer) stays up
        wf_a = _wf_worker(env, AsyncPipeline)
        await wf_a.__aenter__()
        handle = await env.client.start_workflow(
            AsyncPipeline.run, req, id="wf-s3", task_queue=_WF_QUEUE
        )
        # Wait until the activity has been handed off (compute started)...
        await _wait(lambda: len(recorder.starts) >= 1)
        # ...then kill the workflow worker. The async compute is unaffected.
        await wf_a.__aexit__(None, None, None)

        async with _wf_worker(env, AsyncPipeline):
            result = await handle.result()

    assert result.checksum == 7 * (10 * 11 // 2)
    assert recorder.starts.count("wf-s3") == 1, "compute must not have been re-dispatched"
    assert recorder.finishes.count("wf-s3") == 1, "exactly one completion (dup-safe)"
