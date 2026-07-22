"""Read model for the execution runtime: workers + queues (AN-035).

Reads the authoritative ``worker`` registry from Postgres and overlays live
health from Redis (the worker's liveness TTL). Neither Temporal nor Ray is
required, so these endpoints work even when orchestration is degraded.
"""

from __future__ import annotations

import contextlib

from ancora_api.schemas import QueueOut, WorkerOut
from ancora_common import db, registry
from ancora_common.resources import ALL_CAPABILITY_QUEUES, WORKFLOW_TASK_QUEUE, capability_for


def _status_from_liveness(live: bool | None) -> str:
    if live is None:
        return "unknown"
    return "live" if live else "stale"


class WorkerService:
    def __init__(self, redis_url: str) -> None:
        self._redis = registry.redis_client(redis_url)

    async def list_workers(self) -> list[WorkerOut]:
        async with db.session_scope() as session:
            rows = await registry.list_workers(session)
            out: list[WorkerOut] = []
            for w in rows:
                live = await registry.is_live(self._redis, w.worker_id)
                out.append(
                    WorkerOut(
                        worker_id=w.worker_id,
                        host=w.host,
                        pid=w.pid,
                        pools=list(w.pools),
                        task_queues=list(w.task_queues),
                        resources=dict(w.resources),
                        status=_status_from_liveness(live),
                        registered_at=w.registered_at,
                        last_heartbeat_at=w.last_heartbeat_at,
                    )
                )
            return out

    async def list_queues(self) -> list[QueueOut]:
        workers = await self.list_workers()
        queues = [WORKFLOW_TASK_QUEUE, *ALL_CAPABILITY_QUEUES]
        out: list[QueueOut] = []
        for q in queues:
            serving = [w for w in workers if q in w.task_queues]
            live = [w for w in serving if w.status == "live"]
            cap = capability_for(q)
            out.append(
                QueueOut(
                    queue=q,
                    capability=cap.value if cap else None,
                    worker_count=len(serving),
                    live_worker_count=len(live),
                    backlog=0,
                )
            )
        return out

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._redis.aclose()
