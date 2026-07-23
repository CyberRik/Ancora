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


# --------------------------------------------------------------------------- #
# Live activity state (real Temporal state for the durability demo)
# --------------------------------------------------------------------------- #
class RunActivityOut(BaseModel):
    """A pending activity as Temporal sees it right now — the real attempt/failure.

    ``attempt`` incrementing (and ``last_failure`` appearing) is the ground-truth
    signal that a worker died and the step is being retried — no simulation.
    """

    activity_id: str
    activity_type: str
    state: str  # Scheduled | Started | CancelRequested
    attempt: int
    maximum_attempts: int
    last_failure: str | None = None
    last_worker_identity: str | None = None


class RunLiveOut(BaseModel):
    run_id: uuid.UUID
    status: str
    # Optional workflow-reported progress note (``current_status`` query, if any).
    status_note: str | None = None
    activities: list[RunActivityOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Cost accounting (Phase 3, AN-056/AN-057)
# --------------------------------------------------------------------------- #
class CostLineOut(BaseModel):
    """One node execution's contribution to a run's bill."""

    node_id: str
    node_type: str
    attempt: int
    provider: str | None
    model: str | None
    usd: float
    input_tokens: int
    output_tokens: int
    gpu_seconds: float
    created_at: datetime


class CostGroupOut(BaseModel):
    """A rollup slice — by node, by model, or by provider."""

    key: str
    usd: float
    input_tokens: int
    output_tokens: int
    calls: int


class RunCostOut(BaseModel):
    run_id: uuid.UUID
    total_usd: float
    input_tokens: int
    output_tokens: int
    gpu_seconds: float
    by_node: list[CostGroupOut] = Field(default_factory=list)
    by_model: list[CostGroupOut] = Field(default_factory=list)
    by_provider: list[CostGroupOut] = Field(default_factory=list)
    lines: list[CostLineOut] = Field(default_factory=list)


class RetryAttemptOut(BaseModel):
    """A failed attempt and the classification that decided whether it retried."""

    node_id: str
    node_type: str
    attempt: int
    error: str | None
    transient: bool
    retry_after_seconds: float | None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Human-in-the-loop (Phase 3, AN-063/AN-064)
# --------------------------------------------------------------------------- #
class ApprovalOut(BaseModel):
    """A gate in the approval inbox. ``run_id`` is None if the run was pruned."""

    id: uuid.UUID
    run_id: uuid.UUID | None
    temporal_wf_id: str
    gate_id: str
    workflow_name: str | None
    status: str  # waiting | approved | rejected | expired
    prompt: str | None
    payload: dict[str, Any] | None
    requested_at: datetime
    expires_at: datetime | None
    decided_at: datetime | None
    decided_by: str | None
    comment: str | None


class ApprovalDecisionIn(BaseModel):
    approved: bool
    comment: str = ""
    decided_by: str | None = None


# --------------------------------------------------------------------------- #
# Node catalog (Phase 3, AN-058) — the built-ins today, plugins in Phase 5.
# --------------------------------------------------------------------------- #
class ChaosTargetOut(BaseModel):
    """A container the Chaos Lab may act on."""

    service: str
    name: str
    state: str
    killable: bool


class ChaosInjectRequest(BaseModel):
    # "kill" (SIGKILL, no drain) or "restart".
    action: str = "kill"
    service: str
    signal: str = "SIGKILL"


class ChaosEventOut(BaseModel):
    action: str
    service: str
    at: float
    detail: str = ""


class ChaosStatusOut(BaseModel):
    enabled: bool
    project: str
    targets: list[ChaosTargetOut] = Field(default_factory=list)
    events: list[ChaosEventOut] = Field(default_factory=list)
    # Why chaos is unavailable, when it is.
    reason: str | None = None


class NodeTypeOut(BaseModel):
    type_name: str
    version: str
    summary: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    resources: dict[str, Any]
    sandbox: str
    idempotent: bool
    # "builtin" now; "plugin" once the registry lands (Phase 5).
    origin: str = "builtin"
