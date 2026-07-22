"""Inbox idempotency guard (AN-061).

The guard turns an at-least-once activity into an exactly-once *effect*: the first
attempt runs the side effect and stores its result under the idempotency key; any
later attempt (retry, replay, duplicate signal) finds the stored result and
returns it without running the effect again.

Two implementations share the :class:`InboxGuard` protocol: an
:class:`InMemoryInboxGuard` for unit tests / single-process dev, and a
:class:`PostgresInboxGuard` backed by the ``inbox`` table for production. Both are
safe against the *sequential* retries Temporal performs for one activity; the
Postgres unique constraint additionally guards the rare cross-activity race.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ancora_common.db import session_scope
from ancora_common.models import Inbox

Result = dict[str, Any]
Effect = Callable[[], Awaitable[Result]]


class InboxGuard(Protocol):
    async def get_or_run(
        self,
        key: str,
        effect: Effect,
        *,
        temporal_wf_id: str | None = None,
        node_id: str | None = None,
    ) -> Result:
        """Return the stored result for ``key`` or run ``effect`` once and store it."""
        ...


class InMemoryInboxGuard:
    """Process-local guard for tests and single-worker dev."""

    def __init__(self) -> None:
        self._store: dict[str, Result] = {}
        self.effect_runs = 0  # test visibility: how many times the effect actually ran

    async def get_or_run(
        self,
        key: str,
        effect: Effect,
        *,
        temporal_wf_id: str | None = None,
        node_id: str | None = None,
    ) -> Result:
        if key in self._store:
            return self._store[key]
        self.effect_runs += 1
        result = await effect()
        self._store[key] = result
        return result


class PostgresInboxGuard:
    """Durable guard backed by the ``inbox`` table.

    Reserves the key with an ``ON CONFLICT DO NOTHING`` insert. If we won the
    reservation we run the effect and persist the result; otherwise a prior
    attempt already did (or is doing) so — we return its stored result.
    """

    async def get_or_run(
        self,
        key: str,
        effect: Effect,
        *,
        temporal_wf_id: str | None = None,
        node_id: str | None = None,
    ) -> Result:
        async with session_scope() as session:
            existing = await session.execute(select(Inbox).where(Inbox.key == key))
            row = existing.scalar_one_or_none()
            if row is not None and row.status == "done" and row.result is not None:
                return row.result

            if row is None:
                # Reserve the key; DO NOTHING means a concurrent attempt beat us.
                await session.execute(
                    pg_insert(Inbox)
                    .values(
                        key=key, temporal_wf_id=temporal_wf_id, node_id=node_id, status="pending"
                    )
                    .on_conflict_do_nothing(index_elements=["key"])
                )

        result = await effect()

        async with session_scope() as session:
            await session.execute(
                pg_insert(Inbox)
                .values(
                    key=key,
                    temporal_wf_id=temporal_wf_id,
                    node_id=node_id,
                    status="done",
                    result=result,
                    completed_at=datetime.now(UTC),
                )
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_={"status": "done", "result": result, "completed_at": datetime.now(UTC)},
                )
            )
        return result
