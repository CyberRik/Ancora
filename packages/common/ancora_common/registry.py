"""Worker registry: durable capabilities in Postgres, live health in Redis (AN-032).

Split of concerns:
  * **Postgres** (``worker`` table) holds the *authoritative* registration —
    which pools/queues a worker serves and what resources it advertises. It
    survives restarts and is what ``GET /v1/workers`` reads.
  * **Redis** holds a short-TTL liveness key per worker. The worker refreshes it
    every heartbeat; if the process dies the key lapses and the worker is
    reported *stale* even though its row remains.

Both are best-effort at the edges: a Redis outage degrades liveness to "unknown"
rather than crashing a worker, and callers can run without Redis at all (health
falls back to the DB ``last_heartbeat_at`` timestamp).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ancora_common.models import Worker

logger = logging.getLogger("ancora.registry")

# Redis key namespace for worker liveness.
_LIVENESS_PREFIX = "ancora:worker:live:"


def liveness_key(worker_id: str) -> str:
    return f"{_LIVENESS_PREFIX}{worker_id}"


async def upsert_worker(
    session: AsyncSession,
    *,
    worker_id: str,
    pools: list[str],
    task_queues: list[str],
    resources: dict[str, Any],
    host: str | None = None,
    pid: int | None = None,
) -> None:
    """Insert or refresh a worker's registration row (idempotent on ``worker_id``)."""
    now = datetime.now(UTC)
    stmt = pg_insert(Worker).values(
        worker_id=worker_id,
        host=host,
        pid=pid,
        pools=pools,
        task_queues=task_queues,
        resources=resources,
        last_heartbeat_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Worker.worker_id],
        set_={
            "host": host,
            "pid": pid,
            "pools": pools,
            "task_queues": task_queues,
            "resources": resources,
            "last_heartbeat_at": now,
        },
    )
    await session.execute(stmt)


async def touch_worker_heartbeat(session: AsyncSession, worker_id: str) -> None:
    """Bump the DB heartbeat timestamp (a durable fallback for Redis liveness)."""
    result = await session.execute(select(Worker).where(Worker.worker_id == worker_id))
    row = result.scalar_one_or_none()
    if row is not None:
        row.last_heartbeat_at = datetime.now(UTC)


async def deregister_worker(session: AsyncSession, worker_id: str) -> None:
    """Remove a worker's registration (called on graceful drain, AN-031)."""
    result = await session.execute(select(Worker).where(Worker.worker_id == worker_id))
    row = result.scalar_one_or_none()
    if row is not None:
        await session.delete(row)


async def list_workers(session: AsyncSession) -> list[Worker]:
    result = await session.execute(select(Worker).order_by(Worker.worker_id))
    return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Redis liveness
# --------------------------------------------------------------------------- #
def redis_client(redis_url: str) -> aioredis.Redis:
    """A decoded-string async Redis client (caller owns its lifecycle)."""
    return aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)


async def set_liveness(client: aioredis.Redis, worker_id: str, ttl_seconds: int) -> None:
    """Refresh the worker's liveness key with a fresh TTL. Best-effort."""
    try:
        await client.set(liveness_key(worker_id), datetime.now(UTC).isoformat(), ex=ttl_seconds)
    except Exception as exc:  # noqa: BLE001 — liveness must never crash a worker
        logger.warning("redis liveness set failed for %s: %s", worker_id, exc)


async def clear_liveness(client: aioredis.Redis, worker_id: str) -> None:
    try:
        await client.delete(liveness_key(worker_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis liveness clear failed for %s: %s", worker_id, exc)


async def is_live(client: aioredis.Redis, worker_id: str) -> bool | None:
    """True/False from Redis, or ``None`` if Redis itself is unreachable."""
    try:
        return bool(await client.exists(liveness_key(worker_id)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis liveness check failed for %s: %s", worker_id, exc)
        return None
