"""DB-backed node-execution recorder (the crude Phase-2 projection).

Writes/updates a ``node_execution`` row per dispatch. Best-effort: a DB hiccup
must never fail an activity, so every write swallows and logs. Replaced by the
event-sourced consumer in Phase 4.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ancora_common.db import session_scope
from ancora_common.models import NodeExecution

logger = logging.getLogger("ancora.runtime.recorder")


class DbNodeRecorder:
    """Implements the ``runtime.NodeRecorder`` protocol against Postgres."""

    def __init__(self, worker_id: str) -> None:
        self._worker_id = worker_id

    async def record_start(self, meta: dict[str, Any]) -> None:
        try:
            async with session_scope() as session:
                session.add(
                    NodeExecution(
                        temporal_wf_id=str(meta.get("temporal_wf_id", "")),
                        node_name=str(meta.get("node_name", "")),
                        capability=str(meta.get("capability", "cpu")),
                        backend=str(meta.get("backend", "local")),
                        ray_task_id=meta.get("ray_task_id"),
                        worker_id=self._worker_id,
                        status="Running",
                        attempt=int(meta.get("attempt", 1)),
                        started_at=datetime.now(UTC),
                    )
                )
        except Exception as exc:  # noqa: BLE001 — projection is best-effort
            logger.warning("record_start failed: %s", exc)

    async def record_finish(self, meta: dict[str, Any]) -> None:
        # Insert a terminal row; the projection is append-oriented and thin.
        try:
            async with session_scope() as session:
                session.add(
                    NodeExecution(
                        temporal_wf_id=str(meta.get("temporal_wf_id", "")),
                        node_name=str(meta.get("node_name", "")),
                        capability=str(meta.get("capability", "cpu")),
                        backend=str(meta.get("backend", "local")),
                        ray_task_id=meta.get("ray_task_id"),
                        worker_id=self._worker_id,
                        status=str(meta.get("status", "Completed")),
                        attempt=int(meta.get("attempt", 1)),
                        closed_at=datetime.now(UTC),
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("record_finish failed: %s", exc)
