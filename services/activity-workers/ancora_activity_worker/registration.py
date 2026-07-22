"""Worker registration + liveness heartbeat loop (AN-032).

On startup the worker upserts its capabilities into Postgres and starts a task
that refreshes a Redis liveness TTL every ``heartbeat_interval``. On drain it
clears the Redis key and removes its row so the control plane sees it leave
promptly (AN-031). All of it is best-effort: losing Redis or Postgres degrades
observability, it does not stop the worker from doing durable work.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from ancora_activity_worker.settings import ActivityWorkerSettings
from ancora_common import db, registry
from ancora_common.resources import queue_for

logger = logging.getLogger("ancora.runtime.registration")


class WorkerRegistration:
    def __init__(self, settings: ActivityWorkerSettings) -> None:
        self._s = settings
        self._redis = registry.redis_client(settings.redis_url)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self._register()
        self._task = asyncio.create_task(self._heartbeat_loop())

    async def _register(self) -> None:
        s = self._s
        pools = [p.value for p in s.pools]
        queues = [queue_for(p) for p in s.pools]
        resources = {
            "total_cpus": s.total_cpus,
            "total_gpus": s.total_gpus,
            "accelerator_type": s.accelerator_type,
        }
        try:
            async with db.session_scope() as session:
                await registry.upsert_worker(
                    session,
                    worker_id=s.worker_id,
                    pools=pools,
                    task_queues=queues,
                    resources=resources,
                    host=os.uname().nodename if hasattr(os, "uname") else None,
                    pid=os.getpid(),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("worker DB registration failed: %s", exc)
        await registry.set_liveness(self._redis, s.worker_id, s.liveness_ttl_seconds)
        logger.info("registered worker", extra={"worker_id": s.worker_id, "pools": pools})

    async def _heartbeat_loop(self) -> None:
        s = self._s
        try:
            while True:
                await asyncio.sleep(s.heartbeat_interval_seconds)
                await registry.set_liveness(self._redis, s.worker_id, s.liveness_ttl_seconds)
                try:
                    async with db.session_scope() as session:
                        await registry.touch_worker_heartbeat(session, s.worker_id)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("heartbeat DB touch failed: %s", exc)
        except asyncio.CancelledError:
            raise

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        try:
            async with db.session_scope() as session:
                await registry.deregister_worker(session, self._s.worker_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("worker deregistration failed: %s", exc)
        await registry.clear_liveness(self._redis, self._s.worker_id)
        with contextlib.suppress(Exception):
            await self._redis.aclose()
        logger.info("deregistered worker", extra={"worker_id": self._s.worker_id})
