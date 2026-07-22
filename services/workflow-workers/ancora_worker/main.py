"""Workflow worker entrypoint.

Connects to Temporal, reports the workflow catalog, then runs a Worker that serves
both workflows and (Phase 1) their activities inline until SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from temporalio.worker import Worker

from ancora_common.logging import configure_logging
from ancora_common.temporal import connect
from ancora_worker.catalog_report import report_catalog
from ancora_worker.examples import ACTIVITIES, WORKFLOWS
from ancora_worker.settings import WorkerSettings

logger = logging.getLogger("ancora.worker")


async def _run() -> None:
    settings = WorkerSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    client = await connect(settings.temporal_address, settings.temporal_namespace)

    if settings.report_catalog:
        try:
            await report_catalog(settings.task_queue)
        except Exception as exc:  # noqa: BLE001 — catalog is best-effort at boot
            logger.warning("catalog report failed (continuing): %s", exc)

    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=WORKFLOWS,
        activities=ACTIVITIES,
        max_concurrent_activities=settings.max_concurrent_activities,
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
    logger.info("worker draining complete; exiting")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
