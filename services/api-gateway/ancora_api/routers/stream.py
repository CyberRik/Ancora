"""WebSocket live-stream endpoints (Phase 4, RFC-0001 §8).

The browser opens a socket per run and receives lifecycle events as work happens,
so the DAG animates without polling Temporal on the hot path. Two properties make
this reconnect-safe and cheap:

* **Replay by id.** Events are read from the run's per-run Redis stream. A client
  reconnects with the ``last_id`` it last saw (``?last_id=``) and receives exactly
  the events it missed — no gap, no full refetch. A fresh client passes ``0`` (or
  omits it) and gets the buffered tail to warm up.

* **No Temporal on the socket.** Resolving a run to its workflow id is a single
  projection read, so the stream works even while Temporal is unreachable — it is
  replaying the durable event log, not querying the cluster.

The event-to-frame mapping is a pure function so the wire format is unit-tested
without a socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ancora_api.deps import get_worker_service
from ancora_common.db import session_scope
from ancora_common.events import EventBus, RunEvent
from ancora_common.models import WorkflowRun

logger = logging.getLogger("ancora.api.stream")

router = APIRouter(prefix="/v1/stream", tags=["stream"])

# Close codes (application range) for a run that does not exist / bad id.
_CLOSE_NOT_FOUND = 4404
_CLOSE_BAD_REQUEST = 4400

# How long a workers-stream snapshot waits between pushes.
_WORKERS_INTERVAL = 2.0


def event_frame(stream_id: str, event: RunEvent) -> dict[str, Any]:
    """Map a stream entry to the JSON frame a browser receives (pure)."""
    return {
        "type": "event",
        "id": stream_id,
        "kind": event.kind,
        "node_id": event.node_id,
        "activity_id": event.activity_id,
        "activity_type": event.activity_type,
        "attempt": event.attempt,
        "worker_id": event.worker_id,
        "status": event.status,
        "error": event.error,
        "ts": event.ts.isoformat(),
    }


async def _resolve_run(run_id: uuid.UUID) -> tuple[str, str] | None:
    """Return ``(temporal_wf_id, status)`` for a run, or None if unknown.

    A projection read — deliberately independent of Temporal, so the live stream
    keeps working when the cluster is down.
    """
    async with session_scope() as session:
        row = (
            await session.execute(
                select(WorkflowRun.temporal_wf_id, WorkflowRun.status).where(
                    WorkflowRun.id == run_id
                )
            )
        ).first()
    if row is None:
        return None
    return str(row[0]), str(row[1])


@router.websocket("/runs/{run_id}")
async def stream_run(websocket: WebSocket, run_id: str) -> None:
    await websocket.accept()

    try:
        parsed = uuid.UUID(run_id)
    except ValueError:
        await websocket.close(code=_CLOSE_BAD_REQUEST)
        return

    resolved = await _resolve_run(parsed)
    if resolved is None:
        await websocket.close(code=_CLOSE_NOT_FOUND)
        return
    wf_id, status = resolved

    # A fresh client omits last_id and gets the buffered tail; a reconnecting one
    # passes the id it last saw and gets only what it missed.
    last_id = websocket.query_params.get("last_id") or "0"
    bus: EventBus = websocket.app.state.event_bus

    await websocket.send_json({"type": "hello", "wf_id": wf_id, "status": status})

    try:
        while True:
            batch = await bus.tail_run(wf_id, last_id=last_id, block_ms=15000)
            if not batch:
                # Idle window: a keepalive both holds the socket open through
                # proxies and surfaces a client disconnect as a send error.
                await websocket.send_json({"type": "heartbeat"})
                continue
            for stream_id, event in batch:
                last_id = stream_id
                await websocket.send_json(event_frame(stream_id, event))
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001 — a Redis blip must not 500 the socket
        logger.warning("run stream error (wf=%s): %s", wf_id, exc)
        await _safe_close(websocket)


@router.websocket("/workers")
async def stream_workers(websocket: WebSocket) -> None:
    """Push periodic worker-fleet snapshots (registry + live health)."""
    await websocket.accept()
    workers = get_worker_service()
    try:
        while True:
            try:
                fleet = await workers.list_workers()
                payload = [w.model_dump(mode="json") for w in fleet]
                await websocket.send_json({"type": "workers", "workers": payload})
            except Exception as exc:  # noqa: BLE001 — registry overlay, keep streaming
                logger.debug("workers snapshot failed: %s", exc)
            await asyncio.sleep(_WORKERS_INTERVAL)
    except WebSocketDisconnect:
        return


async def _safe_close(websocket: WebSocket) -> None:
    with contextlib.suppress(Exception):
        await websocket.close()
