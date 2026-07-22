"""Request/response models for the API (Phase 1: workflows + runs)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowVersionOut(BaseModel):
    version: int
    code_hash: str
    determinism_token: str
    task_queue: str
    created_at: datetime


class WorkflowDefOut(BaseModel):
    id: uuid.UUID
    name: str
    latest_version: int | None
    versions: list[int]
    created_at: datetime


class StartRunRequest(BaseModel):
    input: dict[str, Any] | None = Field(default=None, description="Workflow input.")
    version: int | None = Field(default=None, description="Pin a version; omit for latest.")


class RunOut(BaseModel):
    id: uuid.UUID
    workflow_name: str
    version: int
    temporal_wf_id: str
    temporal_run_id: str
    status: str
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    error: str | None
    started_at: datetime | None
    closed_at: datetime | None
    created_at: datetime


class RunLinks(BaseModel):
    self: str
    # WebSocket stream lands in Phase 4; advertised here for forward-compat.
    stream: str


class StartRunResponse(BaseModel):
    run_id: uuid.UUID
    temporal_wf_id: str
    status: str
    links: RunLinks


# --------------------------------------------------------------------------- #
# Execution runtime (Phase 2)
# --------------------------------------------------------------------------- #
class WorkerOut(BaseModel):
    worker_id: str
    host: str | None
    pid: int | None
    pools: list[str]
    task_queues: list[str]
    resources: dict[str, Any]
    # "live" (Redis TTL present), "stale" (row exists, TTL lapsed), or "unknown"
    # (Redis unreachable — fall back to last_heartbeat_at).
    status: str
    registered_at: datetime
    last_heartbeat_at: datetime | None


class QueueOut(BaseModel):
    queue: str
    capability: str | None
    worker_count: int
    live_worker_count: int
    # Pending demand; wired to the scheduler's watermark in Phase 3 (0 for now).
    backlog: int
