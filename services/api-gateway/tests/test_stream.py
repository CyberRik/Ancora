"""Tests for the live WebSocket stream: frame mapping and a socket round-trip.

The frame is the contract the browser animates against, so its shape is pinned.
The round-trip proves the reconnect-safe machinery end to end without Redis: a
fake bus hands the endpoint a scripted batch, and the client must receive the
``hello`` greeting followed by exactly those events, in order, as ``event`` frames.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from ancora_api.routers import stream
from ancora_common.events import EventKind, RunEvent


def test_event_frame_shape() -> None:
    event = RunEvent(
        kind=EventKind.ACTIVITY_COMPLETED,
        wf_id="wf-1",
        run_id="run-1",
        ts=datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC),
        node_id="summarize-0",
        activity_id="5",
        activity_type="run_node",
        attempt=2,
        worker_id="aw-1",
        status="Completed",
    )
    frame = stream.event_frame("15-0", event)
    assert frame == {
        "type": "event",
        "id": "15-0",
        "kind": "activity.completed",
        "node_id": "summarize-0",
        "activity_id": "5",
        "activity_type": "run_node",
        "attempt": 2,
        "worker_id": "aw-1",
        "status": "Completed",
        "error": None,
        "ts": "2026-07-26T12:00:00+00:00",
    }


class _ScriptedBus:
    """Hands the endpoint one batch, then blocks-lite so no busy-loop of heartbeats."""

    def __init__(self, batch: list[tuple[str, RunEvent]]) -> None:
        self._batch = batch
        self._served = False

    async def tail_run(self, wf_id: str, *, last_id: str, block_ms: int) -> Any:
        if not self._served:
            self._served = True
            return self._batch
        await asyncio.sleep(0.02)  # mimic a blocking read with no new events
        return []


def _app(bus: _ScriptedBus) -> FastAPI:
    app = FastAPI()
    app.include_router(stream.router)
    app.state.event_bus = bus
    return app


def test_stream_sends_hello_then_events(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = "00000000-0000-0000-0000-0000000000ab"

    async def fake_resolve(_rid: Any) -> tuple[str, str]:
        return ("wf-42", "Running")

    monkeypatch.setattr(stream, "_resolve_run", fake_resolve)

    batch = [
        ("10-0", RunEvent(kind=EventKind.ACTIVITY_STARTED, wf_id="wf-42", node_id="search")),
        ("11-0", RunEvent(kind=EventKind.ACTIVITY_COMPLETED, wf_id="wf-42", node_id="search")),
    ]
    client = TestClient(_app(_ScriptedBus(batch)))
    with client.websocket_connect(f"/v1/stream/runs/{run_id}") as ws:
        hello = ws.receive_json()
        assert hello == {"type": "hello", "wf_id": "wf-42", "status": "Running"}
        first = ws.receive_json()
        assert first["type"] == "event" and first["id"] == "10-0"
        assert first["node_id"] == "search" and first["kind"] == "activity.started"
        second = ws.receive_json()
        assert second["id"] == "11-0" and second["kind"] == "activity.completed"


def test_stream_rejects_bad_uuid() -> None:
    client = TestClient(_app(_ScriptedBus([])))
    with client.websocket_connect("/v1/stream/runs/not-a-uuid") as ws:
        # The server accepts then closes with the bad-request code; the client
        # observes the close frame.
        data = ws.receive()
        assert data["type"] == "websocket.close"
        assert data["code"] == stream._CLOSE_BAD_REQUEST


def test_stream_unknown_run_closes_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(_rid: Any) -> None:
        return None

    monkeypatch.setattr(stream, "_resolve_run", fake_resolve)
    client = TestClient(_app(_ScriptedBus([])))
    with client.websocket_connect("/v1/stream/runs/00000000-0000-0000-0000-0000000000ff") as ws:
        data = ws.receive()
        assert data["type"] == "websocket.close"
        assert data["code"] == stream._CLOSE_NOT_FOUND
