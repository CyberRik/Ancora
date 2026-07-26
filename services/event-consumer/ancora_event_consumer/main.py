"""Event-consumer entrypoint (Phase 4).

Runs two cooperating loops until SIGTERM/SIGINT:

* the **projector** drains the Redis Streams event bus into ``run_event``;
* the **reconciler** settles run status from Temporal history and emits run-level
  lifecycle events.

Either can be toggled off independently. The process is stateless and safe to run
as multiple replicas — the projector's consumer group shares the work, and the
reconciler's updates are idempotent.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from ancora_common.events import EventBus
from ancora_common.logging import configure_logging
from ancora_common.temporal import connect
from ancora_event_consumer.http import serve
from ancora_event_consumer.projector import Projector
from ancora_event_consumer.reconciler import Reconciler
from ancora_event_consumer.settings import ConsumerSettings

logger = logging.getLogger("ancora.consumer")


async def _run() -> None:
    settings = ConsumerSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    bus = EventBus(settings.redis_url)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    tasks: list[asyncio.Task[None]] = []
    client = None
    if settings.run_projector:
        tasks.append(asyncio.create_task(Projector(bus, settings).run_forever(stop)))
    if settings.run_reconciler:
        client = await connect(settings.temporal_address, settings.temporal_namespace)
        tasks.append(asyncio.create_task(Reconciler(client, bus, settings).run_forever(stop)))

    if not tasks:
        logger.warning("both projector and reconciler disabled; nothing to do")
        return

    # Health + Prometheus surface, alongside the loops (Phase 4c).
    tasks.append(asyncio.create_task(serve(settings.metrics_port, stop)))

    logger.info("event consumer started")
    await stop.wait()
    logger.info("shutdown signal received; draining")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await bus.aclose()
    logger.info("event consumer exited cleanly")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
