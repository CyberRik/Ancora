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
# Recovery view — why a run pauses after a kill, and how it rebuilds
# --------------------------------------------------------------------------- #
class RecoverySpanOut(BaseModel):
    """One attempt of one activity, placed on a time axis.

    ``outcome="lost"`` is the attempt (or attempts) that died with a worker.
    Temporal never persists the start event of an attempt that fails, so such a
    span is a *bound*, not a measurement — hence ``approximate``. What is known
    exactly: it cannot have begun before the activity was scheduled, and it was
    over by the time the next attempt started.
    """

    activity_id: str
    node_id: str
    activity_type: str
    attempt: int
    # Temporal worker identity ("<pid>@<host>"), or None for a lost attempt —
    # the process died before the server recorded which one it was.
    worker: str | None = None
    # completed | failed | timed_out | canceled | running | queued | lost
    outcome: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    failure: str | None = None
    # How many attempts this span stands in for (>1 only when retries piled up).
    lost_attempts: int = 0
    approximate: bool = False


class RecoveryMarkerOut(BaseModel):
    """A discrete fleet event worth drawing as a line across the timeline."""

    at: datetime
    # kill | restart | worker_changed | workflow_task_timeout
    kind: str
    label: str
    detail: str | None = None


class RecoveryWindowOut(BaseModel):
    """A clock the run is currently waiting on — the reason nothing is moving.

    Three waits are possible after a kill and they are not interchangeable:
    ``queued`` (nobody is polling the task queue), ``detecting`` (an attempt is
    held by a process that is gone and the server must wait out the timeout that
    attempt was granted), and ``backoff`` (the attempt already failed and the
    retry policy is holding the next one back). Only ``detecting`` is a design
    decision — its length is whatever ``start_to_close``/``heartbeat`` says.
    """

    activity_id: str
    node_id: str
    # detecting | backoff | queued | workflow_task
    kind: str
    # start_to_close | heartbeat | retry_backoff | none
    clock: str | None = None
    attempt: int = 1
    worker: str | None = None
    # live | replaced | gone | unknown
    worker_state: str = "unknown"
    queue: str | None = None
    queue_has_worker: bool | None = None
    started_at: datetime | None = None
    deadline_at: datetime | None = None
    timeout_seconds: float | None = None
    elapsed_seconds: float = 0.0
    remaining_seconds: float | None = None
    heartbeat_at: datetime | None = None
    heartbeat_timeout_seconds: float | None = None
    reason: str = ""


class RunRecoveryOut(BaseModel):
    run_id: uuid.UUID
    status: str
    # Server clock, so the UI animates against the same time base as the deadlines.
    now: datetime
    # Distinct worker identities that touched this run, in first-seen order.
    workers: list[str] = Field(default_factory=list)
    spans: list[RecoverySpanOut] = Field(default_factory=list)
    markers: list[RecoveryMarkerOut] = Field(default_factory=list)
    windows: list[RecoveryWindowOut] = Field(default_factory=list)
    # Recorded activity results a replacement worker rebuilt state from without
    # re-executing them — the exactly-once claim, as a number.
    replayed_activities: int = 0
    # Times the work changed hands between processes.
    handoffs: int = 0


# --------------------------------------------------------------------------- #
# Run graph — the DAG the run actually executed, with live node states
# --------------------------------------------------------------------------- #
class GraphNodeOut(BaseModel):
    """One vertex of a run's DAG, collapsed across its attempts.

    A node is a *unit of durable work*, not an attempt: retries reuse the same
    scheduled event, so a node that died with a worker and succeeded on attempt 2
    is one vertex carrying ``attempts=2``. The per-attempt story is the recovery
    view's job.
    """

    id: str
    label: str
    # node | activity | gate | wait
    kind: str
    # For ``kind="node"``: the built-in class (llm, http, python, database).
    node_type: str | None = None
    # Temporal activity type, absent for the synthesized gate and wait vertices.
    activity_type: str | None = None
    activity_id: str | None = None
    # Topological rank: nodes sharing a layer were commanded by one workflow task,
    # i.e. the workflow decided on them together and they may run concurrently.
    layer: int
    # completed | failed | timed_out | canceled | running | retrying | queued | waiting
    state: str
    attempts: int = 1
    # Attempts that ended without a recorded result — the ones a worker took down.
    lost_attempts: int = 0
    worker: str | None = None
    queue: str | None = None
    priority: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    failure: str | None = None
    # Gate vertices only: the decision, once one arrived.
    approved: bool | None = None
    decided_by: str | None = None
    timed_out: bool = False
    # Explanation shown when the DAG alone would not say why a vertex looks stuck.
    note: str | None = None


class GraphEdgeOut(BaseModel):
    """A happens-after relation between two vertices, as Temporal recorded it.

    Edges come from Temporal's own causality: an activity's scheduled event names
    the workflow task that commanded it, so vertices in layer *n+1* were decided
    on only after layer *n*'s results were in hand. That is a **scheduling**
    dependency, which is what history proves — not a data dependency, which it
    does not record. ``done`` marks an edge whose source has a recorded result.
    """

    source: str
    target: str
    done: bool = False


class RunGraphOut(BaseModel):
    """A run's DAG reconstructed from history, with per-vertex live state.

    Reconstructed rather than declared: the shape is whatever the workflow really
    did on this run, including the branch it took, so a graph that expired at a
    gate looks different from one that was approved.
    """

    run_id: uuid.UUID
    workflow_name: str
    status: str
    now: datetime
    nodes: list[GraphNodeOut] = Field(default_factory=list)
    edges: list[GraphEdgeOut] = Field(default_factory=list)
    # Vertices with a recorded result, out of the vertices discovered so far. The
    # denominator grows as the workflow decides on more work — a DAG read from
    # history cannot know steps the workflow has not committed to yet.
    completed: int = 0
    total: int = 0


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
