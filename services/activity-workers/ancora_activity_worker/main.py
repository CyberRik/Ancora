"""Activity worker entrypoint (AN-026, AN-031).

Boots one Temporal ``Worker`` per capability queue the worker serves, wired to
the same activity set. Holds a Temporal client (for async completion), a Ray/local
backend, and a registration/liveness loop. On SIGTERM it stops polling, lets
in-flight inline activities drain, deregisters, and exits 0 — async (handed-off)
work is unaffected because it no longer occupies this process.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from temporalio.worker import Worker

from ancora_activity_worker import runtime
from ancora_activity_worker.activities import ACTIVITIES
from ancora_activity_worker.ray_bridge import connect_backend
from ancora_activity_worker.recorder import DbNodeRecorder
from ancora_activity_worker.registration import WorkerRegistration
from ancora_activity_worker.settings import ActivityWorkerSettings
from ancora_common.logging import configure_logging
from ancora_common.resources import queue_for
from ancora_common.temporal import connect

logger = logging.getLogger("ancora.activity-worker")


async def _run() -> None:
    settings = ActivityWorkerSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    client = await connect(settings.temporal_address, settings.temporal_namespace)

    # Wire the runtime seams the activities read from.
    runtime.set_completion_client(client)
    runtime.set_backend(connect_backend(settings.ray_address))
    runtime.set_node_recorder(DbNodeRecorder(settings.worker_id))

    registration = WorkerRegistration(settings) if settings.register else None
    if registration is not None:
        await registration.start()

    queues = [queue_for(p) for p in settings.pools]
    workers = [
        Worker(
            client,
            task_queue=q,
            activities=ACTIVITIES,
            max_concurrent_activities=settings.max_concurrent_activities,
        )
        for q in queues
    ]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async with contextlib.AsyncExitStack() as stack:
        for w in workers:
            await stack.enter_async_context(w)
        logger.info(
            "activity worker started",
            extra={
                "worker_id": settings.worker_id,
                "queues": queues,
                "backend": runtime.get_backend().name,
            },
        )
        await stop.wait()
        logger.info("SIGTERM received; draining")

    if registration is not None:
        await registration.stop()
    runtime.get_backend().shutdown()
    logger.info("activity worker exited cleanly")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
