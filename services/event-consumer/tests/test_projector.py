"""Unit tests for the projector's mapping and its claim/persist/ack contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from ancora_event_consumer import projector as projector_mod
from ancora_event_consumer.projector import Projector, event_row_values
from ancora_event_consumer.settings import ConsumerSettings

from ancora_common.events import EventKind, RunEvent


def test_event_row_values_maps_all_fields() -> None:
    event = RunEvent(
        kind=EventKind.ACTIVITY_FAILED,
        wf_id="wf-1",
        run_id="run-1",
        ts=datetime(2026, 7, 26, tzinfo=UTC),
        node_id="fetch",
        activity_id="3",
        activity_type="http_call",
        attempt=2,
        worker_id="aw-1",
        status="Failed",
        error="429 rate limited",
        payload={"provider": "openai"},
    )
    row = event_row_values("1690000000000-0", event)
    assert row["stream_id"] == "1690000000000-0"
    assert row["temporal_wf_id"] == "wf-1"
    assert row["kind"] == "activity.failed"
    assert row["attempt"] == 2
    assert row["error"] == "429 rate limited"
    assert row["payload"] == {"provider": "openai"}


def test_event_row_values_empty_payload_becomes_null() -> None:
    row = event_row_values("1-0", RunEvent(kind=EventKind.RUN_STARTED, wf_id="wf"))
    # An empty payload/run_id is stored as NULL, not "{}"/"".
    assert row["payload"] is None
    assert row["temporal_run_id"] is None


class _FakeBus:
    """A stand-in bus that hands the projector a scripted batch and records acks."""

    def __init__(self, batch: list[tuple[str, RunEvent]]) -> None:
        self._batch = batch
        self.acked: list[str] = []

    async def read_group(self, consumer: str, *, count: int, block_ms: int) -> Any:
        return self._batch

    async def ack(self, ids: Any) -> None:
        self.acked.extend(ids)


@pytest.mark.asyncio
async def test_run_once_acks_exactly_the_claimed_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    batch = [
        ("10-0", RunEvent(kind=EventKind.ACTIVITY_STARTED, wf_id="wf")),
        ("11-0", RunEvent(kind=EventKind.ACTIVITY_COMPLETED, wf_id="wf")),
    ]

    async def fake_persist(b: Any) -> int:
        return len(b)

    monkeypatch.setattr(projector_mod, "persist_batch", fake_persist)
    bus = _FakeBus(batch)
    written = await Projector(bus, ConsumerSettings()).run_once()  # type: ignore[arg-type]
    assert written == 2
    assert bus.acked == ["10-0", "11-0"]


@pytest.mark.asyncio
async def test_run_once_empty_batch_does_not_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_persist(b: Any) -> int:  # pragma: no cover — must not be reached
        raise AssertionError("persist_batch called for an empty batch")

    monkeypatch.setattr(projector_mod, "persist_batch", fake_persist)
    bus = _FakeBus([])
    written = await Projector(bus, ConsumerSettings()).run_once()  # type: ignore[arg-type]
    assert written == 0
    assert bus.acked == []
