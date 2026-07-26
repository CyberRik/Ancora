"""Workflow worker entrypoint.

Connects to Temporal, reports the workflow catalog, then runs a Worker that serves
both workflows and (Phase 1) their activities inline until SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import socket

from temporalio.worker import Worker

from ancora_common.event_interceptor import EventInterceptor
from ancora_common.events import EventBus
from ancora_common.logging import configure_logging
from ancora_common.temporal import connect
from ancora_worker.catalog_report import report_catalog
from ancora_worker.examples import ACTIVITIES, WORKFLOWS
from ancora_worker.gate_activities import GATE_ACTIVITIES
from ancora_worker.settings import WorkerSettings

logger = logging.getLogger("ancora.worker")

# Approval-gate bookkeeping rides the orchestration queue (see gate_activities).
# Registered here rather than in examples.py because it reaches the ORM, and
# anything examples.py imports is also imported inside the workflow sandbox —
# where SQLAlchemy is not importable.
ALL_ACTIVITIES = [*ACTIVITIES, *GATE_ACTIVITIES]


async def _run() -> None:
    settings = WorkerSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    client = await connect(settings.temporal_address, settings.temporal_namespace)

    if settings.report_catalog:
        try:
            await report_catalog(settings.task_queue)
        except Exception as exc:  # noqa: BLE001 — catalog is best-effort at boot
            logger.warning("catalog report failed (continuing): %s", exc)

    # Emit activity lifecycle events for the live UI (Phase 4). The interceptor
    # only wraps activities — a workflow interceptor would write to Redis inside
    # the deterministic sandbox and break replay; run-level lifecycle is authored
    # by the event consumer's reconciler instead.
    bus = EventBus(settings.redis_url)
    worker_identity = f"{socket.gethostname()}:workflow"

    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=WORKFLOWS,
        activities=ALL_ACTIVITIES,
        max_concurrent_activities=settings.max_concurrent_activities,
        interceptors=[EventInterceptor(bus, worker_identity)],
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async with worker:
        logger.info(
            "worker started",
            extra={"task_queue": settings.task_queue, "workflows": len(WORKFLOWS)},
        )
        await stop.wait()
    await bus.aclose()
    logger.info("worker draining complete; exiting")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
