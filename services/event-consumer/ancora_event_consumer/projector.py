"""Projector: drain the event bus into the durable ``run_event`` log.

The projector is the "fast path" half of the consumer. It claims events from the
Redis Streams consumer group (at-least-once), writes them to Postgres, and acks.
Two invariants make the at-least-once delivery safe:

* **Idempotent writes.** Each row is keyed by the global stream id
  (``uq_run_event_stream_id``); a redelivered event hits ``ON CONFLICT DO NOTHING``
  and vanishes. So we can ack *after* a successful insert without risking a
  duplicate on the redelivery that a mid-batch crash would cause.

* **Ack only what we wrote.** A batch that fails to persist is left unacked and
  redelivered — never dropped.

Building the insert values is a pure function so the mapping is unit-tested with
no Redis and no database.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from ancora_common.db import session_scope
from ancora_common.events import EventBus, RunEvent
from ancora_common.models import RunEventRow
from ancora_event_consumer._util import sleep_or_stop
from ancora_event_consumer.metrics import EVENTS_PROJECTED, PROJECTOR_BATCHES
from ancora_event_consumer.settings import ConsumerSettings

logger = logging.getLogger("ancora.consumer.projector")


def event_row_values(stream_id: str, event: RunEvent) -> dict[str, Any]:
    """Map a stream entry to a ``run_event`` insert row (pure)."""
    return {
        "stream_id": stream_id,
        "temporal_wf_id": event.wf_id,
        "temporal_run_id": event.run_id or None,
        "kind": event.kind,
        "node_id": event.node_id,
        "activity_id": event.activity_id,
        "activity_type": event.activity_type,
        "attempt": event.attempt,
        "worker_id": event.worker_id,
        "status": event.status,
        "error": event.error,
        "payload": event.payload or None,
        "event_ts": event.ts,
    }


async def persist_batch(batch: Sequence[tuple[str, RunEvent]]) -> int:
    """Insert a batch of events idempotently; return the number newly written."""
    if not batch:
        return 0
    rows = [event_row_values(stream_id, event) for stream_id, event in batch]
    async with session_scope() as session:
        stmt = pg_insert(RunEventRow).values(rows)
        # A redelivered event (same global stream id) is a no-op.
        stmt = stmt.on_conflict_do_nothing(constraint="uq_run_event_stream_id")
        result = await session.execute(stmt)
    # ``rowcount`` counts the rows actually inserted (conflicts excluded).
    written: int = getattr(result, "rowcount", 0) or 0
    return written


class Projector:
    """The projector loop over the consumer group."""

    def __init__(self, bus: EventBus, settings: ConsumerSettings) -> None:
        self._bus = bus
        self._settings = settings

    async def run_once(self) -> int:
        """Claim, persist, and ack one batch; return how many rows were written."""
        batch = await self._bus.read_group(
            self._settings.consumer_name,
            count=self._settings.batch_size,
            block_ms=self._settings.block_ms,
        )
        if not batch:
            return 0
        written = await persist_batch(batch)
        # Safe to ack the whole batch: the insert is idempotent, so even the
        # events that collided on redelivery are durably present.
        await self._bus.ack([stream_id for stream_id, _ in batch])
        PROJECTOR_BATCHES.inc()
        for _stream_id, event in batch:
            EVENTS_PROJECTED.inc(kind=event.kind)
        return written

    async def run_forever(self, stop: asyncio.Event) -> None:
        await self._bus.ensure_group()
        logger.info("projector started (consumer=%s)", self._settings.consumer_name)
        while not stop.is_set():
            try:
                written = await self.run_once()
                if written:
                    logger.debug("projected %d event(s)", written)
            except Exception as exc:  # noqa: BLE001 — keep draining across hiccups
                logger.warning("projector batch failed (retrying): %s", exc)
                await sleep_or_stop(stop, 1.0)
