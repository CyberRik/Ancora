"""The Ancora event bus: lifecycle events over Redis Streams (Phase 4, RFC-0001 §8).

Everything Ancora shows about a *live* run is event-sourced. Workers emit small
lifecycle events as work happens; a consumer projects them to Postgres and fans
them out to the browser. This module is the wire between the two — the event
contract plus a thin Redis Streams client.

Two design points earn their keep:

* **Two streams per event.** Each event is XADD'd to a single global stream
  (``ancora:events``) that a durable consumer group drains into projections
  *and* to a per-run stream (``ancora:run:{wf_id}``) that WebSocket clients tail
  by id — so a browser that drops its socket reconnects with a last-seen id and
  replays exactly the events it missed, with no gap and no full refetch. The
  per-run stream is capped (``MAXLEN``) because it is a live tail, not the log of
  record; the durable log lives in Postgres (``run_event``).

* **Publishing fails open.** A Redis outage must never fail an activity. Every
  publish swallows and logs — a missed live event is healed by the consumer's
  reconciler, which rebuilds projections from Temporal history (the source of
  truth). The bus is an accelerator, never a system of record.

The encode/decode pair is pure (Redis stores flat ``str -> str`` maps), so the
event contract is unit-tested without a live Redis.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

logger = logging.getLogger("ancora.events")

# Stream names and the projector's consumer group.
GLOBAL_STREAM: Final = "ancora:events"
PROJECTOR_GROUP: Final = "projector"

# The global log is trimmed generously (it is drained continuously by the
# consumer group); the per-run tail is short because it only backs live
# reconnects, and the durable history lives in Postgres.
_GLOBAL_MAXLEN: Final = 100_000
_RUN_MAXLEN: Final = 1_000


def run_stream(wf_id: str) -> str:
    """The per-run stream a WebSocket client tails for live updates."""
    return f"ancora:run:{wf_id}"


class EventKind:
    """The lifecycle events the fleet emits.

    Run-level events (``RUN_*``) are authored by the event consumer's reconciler
    from Temporal history — a workflow cannot emit them itself without breaking
    determinism. Activity/node events are authored by the worker-side
    interceptor and the node runtime as work actually happens.
    """

    RUN_STARTED: Final = "run.started"
    RUN_COMPLETED: Final = "run.completed"
    RUN_FAILED: Final = "run.failed"
    RUN_CANCELED: Final = "run.canceled"

    # Emitted by the activity interceptor around every activity execution.
    ACTIVITY_STARTED: Final = "activity.started"
    ACTIVITY_COMPLETED: Final = "activity.completed"
    ACTIVITY_FAILED: Final = "activity.failed"

    # Emitted by the node runtime with backend/placement detail the interceptor
    # cannot see (which backend dispatched, the Ray task id, the capability lane).
    NODE_DISPATCH: Final = "node.dispatch"


@dataclass(frozen=True)
class RunEvent:
    """One lifecycle event for a run.

    ``wf_id``/``run_id`` are Temporal's workflow id and run id. ``activity_id`` is
    Temporal's per-activity id ("5"), stable across a node's retries, so it is the
    natural key that collapses attempts to one vertex. ``payload`` carries
    kind-specific extra fields (backend, ray_task_id, output summary, ...).
    """

    kind: str
    wf_id: str
    run_id: str = ""
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    node_id: str | None = None
    activity_id: str | None = None
    activity_type: str | None = None
    attempt: int = 1
    worker_id: str | None = None
    status: str | None = None
    error: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def encode_event(event: RunEvent) -> dict[str, str]:
    """Flatten an event to the ``str -> str`` map a Redis stream stores."""
    return {
        "kind": event.kind,
        "wf_id": event.wf_id,
        "run_id": event.run_id,
        "ts": event.ts.astimezone(UTC).isoformat(),
        "node_id": _s(event.node_id),
        "activity_id": _s(event.activity_id),
        "activity_type": _s(event.activity_type),
        "attempt": str(event.attempt),
        "worker_id": _s(event.worker_id),
        "status": _s(event.status),
        "error": _s(event.error),
        "payload": json.dumps(event.payload, default=str),
    }


def _opt(fields: Mapping[str, str], key: str) -> str | None:
    value = fields.get(key, "")
    return value or None


def decode_event(fields: Mapping[str, str]) -> RunEvent:
    """Reconstruct an event from a stream entry (inverse of :func:`encode_event`)."""
    ts_raw = fields.get("ts", "")
    try:
        ts = datetime.fromisoformat(ts_raw) if ts_raw else datetime.now(UTC)
    except ValueError:
        ts = datetime.now(UTC)
    try:
        payload = json.loads(fields.get("payload", "") or "{}")
    except (ValueError, TypeError):
        payload = {}
    try:
        attempt = int(fields.get("attempt", "1") or "1")
    except ValueError:
        attempt = 1
    return RunEvent(
        kind=fields.get("kind", ""),
        wf_id=fields.get("wf_id", ""),
        run_id=fields.get("run_id", ""),
        ts=ts,
        node_id=_opt(fields, "node_id"),
        activity_id=_opt(fields, "activity_id"),
        activity_type=_opt(fields, "activity_type"),
        attempt=attempt,
        worker_id=_opt(fields, "worker_id"),
        status=_opt(fields, "status"),
        error=_opt(fields, "error"),
        payload=payload if isinstance(payload, dict) else {},
    )


def node_id_from_args(args: Sequence[Any]) -> str | None:
    """Best-effort node id from an activity's deserialized arguments.

    ``run_node`` receives the node spec as its first argument; the durable node
    identity lives inside it (Temporal's activity id is only a sequence number).
    Any other activity has no node id — return ``None`` rather than guessing.
    """
    if not args:
        return None
    first = args[0]
    if isinstance(first, Mapping):
        for key in ("node_id", "id", "name"):
            value = first.get(key)
            if isinstance(value, str) and value:
                return value
    return None


class EventBus:
    """A thin async Redis Streams publisher/consumer, safe to share per process.

    The client is created lazily so importing this module never opens a socket
    (tests and DB-less paths construct a bus without a live Redis). Every publish
    is best-effort; read/group operations are used only by the consumer, which is
    allowed to fail loudly.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Any | None = None

    def _client(self) -> Any:
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def publish(self, event: RunEvent) -> None:
        """Emit an event to the global log and the run's live tail (fail-open)."""
        fields = encode_event(event)
        try:
            client = self._client()
            pipe = client.pipeline(transaction=False)
            pipe.xadd(GLOBAL_STREAM, fields, maxlen=_GLOBAL_MAXLEN, approximate=True)
            pipe.xadd(run_stream(event.wf_id), fields, maxlen=_RUN_MAXLEN, approximate=True)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001 — a live event must never fail work
            logger.warning("event publish failed (%s): %s", event.kind, exc)

    async def ensure_group(self) -> None:
        """Create the projector consumer group at the log's tail if absent."""
        client = self._client()
        try:
            await client.xgroup_create(GLOBAL_STREAM, PROJECTOR_GROUP, id="0", mkstream=True)
        except Exception as exc:  # noqa: BLE001 — BUSYGROUP means it already exists
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_group(
        self, consumer: str, *, count: int = 128, block_ms: int = 5000
    ) -> list[tuple[str, RunEvent]]:
        """Claim a batch of undelivered events for the projector (at-least-once)."""
        client = self._client()
        try:
            resp = await client.xreadgroup(
                PROJECTOR_GROUP,
                consumer,
                {GLOBAL_STREAM: ">"},
                count=count,
                block=block_ms,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_read_timeout(exc):
                return []  # a blocking read that saw no events is not an error
            raise
        return _flatten(resp)

    async def ack(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        await self._client().xack(GLOBAL_STREAM, PROJECTOR_GROUP, *ids)

    async def tail_run(
        self, wf_id: str, *, last_id: str = "$", count: int = 128, block_ms: int = 15000
    ) -> list[tuple[str, RunEvent]]:
        """Read a run's live tail after ``last_id`` (drives reconnect-safe WS)."""
        client = self._client()
        try:
            resp = await client.xread({run_stream(wf_id): last_id}, count=count, block=block_ms)
        except Exception as exc:  # noqa: BLE001
            if _is_read_timeout(exc):
                return []  # no new events in the window; the caller loops again
            raise
        return _flatten(resp)

    async def backlog(self, wf_id: str, *, count: int = 1000) -> list[tuple[str, RunEvent]]:
        """The run's buffered tail from the start (a fresh WS client's warm-up)."""
        client = self._client()
        resp = await client.xrange(run_stream(wf_id), min="-", max="+", count=count)
        return [(entry_id, decode_event(fields)) for entry_id, fields in resp]

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


def _is_read_timeout(exc: BaseException) -> bool:
    """True for a blocking-read timeout (benign: the window elapsed with no data).

    Matched by class name so this module never imports redis at parse time — a
    ``redis.exceptions.TimeoutError`` (which also subclasses the builtin) counts,
    a genuine connection error does not.
    """
    return type(exc).__name__ == "TimeoutError"


def _flatten(resp: Any) -> list[tuple[str, RunEvent]]:
    """Normalize a Redis stream read (``[[stream, [(id, fields), ...]], ...]``)."""
    out: list[tuple[str, RunEvent]] = []
    if not resp:
        return out
    for _stream, entries in resp:
        for entry_id, fields in entries:
            out.append((entry_id, decode_event(fields)))
    return out
