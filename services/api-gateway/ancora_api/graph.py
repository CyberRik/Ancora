"""The DAG a run actually executed, reconstructed from Temporal's history.

A workflow's graph is not declared anywhere — it is *emergent*. The workflow is
ordinary Python that decides, step by step, what to schedule next, so the shape
of a run depends on its input (how many sources to summarize) and on the branch
it took (approved, or expired at the gate). There is no static diagram that is
true for every run, which is why this module builds one per run.

**Where the edges come from.** Every ``ActivityTaskScheduled`` event names the
workflow task that commanded it (``workflow_task_completed_event_id``). That is
Temporal's own record of causality: activities sharing that id were decided on
together — a fan-out — and anything scheduled by a *later* workflow task was
decided on only once the earlier results were in hand. Grouping by it gives
exact layers, with no timing heuristics involved.

What that proves is a **scheduling** dependency, not a data dependency: history
records that the workflow chose to schedule B after seeing A finish, not that B
consumed A's output. For workflows that fan out and rejoin — which is what the
node library is for — the two coincide. For a workflow that runs two genuinely
independent chains, the layers are still correct but the edges between them
claim an ordering the code did not intend. The schema says so rather than
implying more precision than Temporal has.

**Approval gates get a vertex of their own.** The SDK brackets a durable wait
with ``open_approval_gate`` / ``close_approval_gate`` activities, so the pause is
recoverable exactly: an open with no matching close *is* a run parked at that
gate. The two bookkeeping activities are folded into the single vertex they
describe, because a reader cares about the gate, not about the projection write.

Like :mod:`ancora_api.recovery`, this is a pure function of (history, pending
state, now) so every interesting shape is testable without running a workflow.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio.api.enums.v1 import EventType, PendingActivityState
from temporalio.api.history.v1 import HistoryEvent
from temporalio.api.workflow.v1 import PendingActivityInfo

from ancora_api.history import decode_input, dt, node_label, secs
from ancora_api.schemas import GraphEdgeOut, GraphNodeOut, RunGraphOut

GATE_OPEN = "open_approval_gate"
GATE_CLOSE = "close_approval_gate"


# --------------------------------------------------------------------------- #
# History reduction
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Sched:
    """An ``ActivityTaskScheduled`` event, decoded down to what the DAG needs."""

    event_id: int
    # The workflow task that commanded this activity — the exact layer key.
    decided_by: int
    activity_id: str
    activity_type: str
    task_queue: str
    scheduled_at: datetime | None
    node_id: str | None
    node_type: str | None
    priority: str | None
    gate_id: str | None
    # Present on ``close_approval_gate`` only: how the gate was resolved.
    decision: dict[str, Any] | None


@dataclass
class _Reduction:
    workflow_name: str = ""
    scheduled: dict[int, _Sched] = field(default_factory=dict)
    # scheduled_event_id → (started_at, worker identity, attempt)
    started: dict[int, tuple[datetime | None, str, int]] = field(default_factory=dict)
    # scheduled_event_id → (outcome, ended_at, failure)
    terminal: dict[int, tuple[str, datetime | None, str | None]] = field(default_factory=dict)
    # timer_id → fire time, for timers that are still running
    open_timers: dict[str, datetime | None] = field(default_factory=dict)
    signals: list[str] = field(default_factory=list)
    # True when the last thing the workflow did was finish a decision without
    # scheduling anything — i.e. it is parked, not mid-step.
    parked: bool = False


def _scheduling(decoded: dict[str, Any] | None, key: str) -> str | None:
    """Read a field out of ``run_node``'s admission-control block."""
    if not decoded:
        return None
    block = decoded.get("scheduling")
    if not isinstance(block, dict):
        return None
    value = block.get(key)
    return value if isinstance(value, str) and value else None


def _decode_sched(e: HistoryEvent) -> _Sched:
    sch = e.activity_task_scheduled_event_attributes
    activity_type = sch.activity_type.name
    decoded = decode_input(sch.input)
    gate_id = None
    decision = None
    if activity_type in (GATE_OPEN, GATE_CLOSE) and decoded:
        raw_gate = decoded.get("gate_id")
        gate_id = raw_gate if isinstance(raw_gate, str) and raw_gate else None
        if activity_type == GATE_CLOSE:
            decision = decoded
    node_id = decoded.get("node_id") if decoded else None
    node_type = decoded.get("type_name") if decoded else None
    return _Sched(
        event_id=e.event_id,
        decided_by=int(sch.workflow_task_completed_event_id),
        activity_id=sch.activity_id,
        activity_type=activity_type,
        task_queue=sch.task_queue.name,
        scheduled_at=dt(e.event_time),
        node_id=node_id if isinstance(node_id, str) and node_id else None,
        node_type=node_type if isinstance(node_type, str) and node_type else None,
        priority=_scheduling(decoded, "priority"),
        gate_id=gate_id,
        decision=decision,
    )


def _reduce(events: Sequence[HistoryEvent]) -> _Reduction:
    r = _Reduction()
    for e in events:
        at = dt(e.event_time)
        kind = e.event_type

        if kind == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
            r.workflow_name = e.workflow_execution_started_event_attributes.workflow_type.name

        elif kind == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            r.scheduled[e.event_id] = _decode_sched(e)
            r.parked = False

        elif kind == EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED:
            st = e.activity_task_started_event_attributes
            r.started[st.scheduled_event_id] = (at, st.identity, int(st.attempt) or 1)

        elif kind == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            r.terminal[e.activity_task_completed_event_attributes.scheduled_event_id] = (
                "completed",
                at,
                None,
            )

        elif kind == EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
            bad = e.activity_task_failed_event_attributes
            r.terminal[bad.scheduled_event_id] = ("failed", at, bad.failure.message or None)

        elif kind == EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT:
            late = e.activity_task_timed_out_event_attributes
            r.terminal[late.scheduled_event_id] = ("timed_out", at, late.failure.message or None)

        elif kind == EventType.EVENT_TYPE_ACTIVITY_TASK_CANCELED:
            r.terminal[e.activity_task_canceled_event_attributes.scheduled_event_id] = (
                "canceled",
                at,
                None,
            )

        elif kind == EventType.EVENT_TYPE_TIMER_STARTED:
            ts = e.timer_started_event_attributes
            fire_after = secs(ts.start_to_fire_timeout)
            r.open_timers[ts.timer_id] = (
                at + timedelta(seconds=fire_after) if at is not None and fire_after else None
            )
            r.parked = False

        elif kind == EventType.EVENT_TYPE_TIMER_FIRED:
            r.open_timers.pop(e.timer_fired_event_attributes.timer_id, None)

        elif kind == EventType.EVENT_TYPE_TIMER_CANCELED:
            r.open_timers.pop(e.timer_canceled_event_attributes.timer_id, None)

        elif kind == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED:
            r.signals.append(e.workflow_execution_signaled_event_attributes.signal_name)

        elif kind == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED:
            # Provisionally parked; cleared again if this task went on to
            # schedule something. Commands follow their WorkflowTaskCompleted.
            r.parked = True

    return r


# --------------------------------------------------------------------------- #
# Vertex construction
# --------------------------------------------------------------------------- #
@dataclass
class _Vertex:
    """A graph node under construction, before layers are compacted."""

    node: GraphNodeOut
    layer_key: int


def _activity_state(
    sid: int,
    sched: _Sched,
    r: _Reduction,
    pending: dict[str, PendingActivityInfo],
) -> tuple[str, int, str | None, datetime | None, datetime | None, str | None]:
    """Collapse one activity's attempts into a single state for its vertex.

    History alone under-reports the present: Temporal writes
    ``ActivityTaskStarted`` only once an attempt reaches a terminal event, so the
    attempt in flight right now is invisible in the event stream. Pending state
    supplies it, which is also the only way an attempt stranded on a dead worker
    shows as ``running`` rather than as though it had never left the queue.
    """
    term = r.terminal.get(sid)
    start = r.started.get(sid)
    live = pending.get(sched.activity_id)

    started_at = start[0] if start else None
    worker = (start[1] or None) if start else None
    attempts = start[2] if start else 1

    if live is not None:
        attempts = max(attempts, int(live.attempt) or 1)
        worker = live.last_worker_identity or worker
        started_at = started_at or dt(live.last_started_time)

    if term is not None:
        outcome, ended_at, failure = term
        return outcome, attempts, worker, started_at or sched.scheduled_at, ended_at, failure

    if live is not None:
        failure = live.last_failure.message if live.HasField("last_failure") else None
        if live.state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED:
            return "running", attempts, worker, dt(live.last_started_time), None, failure or None
        # Scheduled again after a failure is a retry; scheduled the first time is
        # simply work nobody has picked up yet.
        state = "retrying" if attempts > 1 else "queued"
        return state, attempts, worker, sched.scheduled_at, None, failure or None

    if start is not None:
        return "running", attempts, worker, started_at, None, None
    return "queued", attempts, None, sched.scheduled_at, None, None


def _duration(started_at: datetime | None, ended_at: datetime | None) -> float | None:
    if started_at is None or ended_at is None:
        return None
    return max(0.0, (ended_at - started_at).total_seconds())


def _activity_vertex(
    sid: int,
    sched: _Sched,
    r: _Reduction,
    pending: dict[str, PendingActivityInfo],
) -> _Vertex:
    state, attempts, worker, started_at, ended_at, failure = _activity_state(sid, sched, r, pending)
    note = None
    if attempts > 1:
        note = (
            f"Attempt {attempts}. The earlier "
            f"{'attempt' if attempts == 2 else 'attempts'} left no result — "
            "rescheduled from history, not re-run from the start of the workflow."
        )
    return _Vertex(
        node=GraphNodeOut(
            id=f"a{sid}",
            label=node_label(sched.activity_type, sched.activity_id, sched.node_id),
            kind="node" if sched.activity_type == "run_node" else "activity",
            node_type=sched.node_type,
            activity_type=sched.activity_type,
            activity_id=sched.activity_id,
            layer=0,
            state=state,
            attempts=attempts,
            lost_attempts=max(0, attempts - 1),
            worker=worker,
            queue=sched.task_queue or None,
            priority=sched.priority,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=_duration(started_at, ended_at),
            failure=failure,
            note=note,
        ),
        layer_key=sched.decided_by,
    )


def _gate_vertices(
    opens: list[tuple[int, _Sched]],
    closes: list[_Sched],
    r: _Reduction,
    terminal_run: bool,
) -> list[_Vertex]:
    """Fold each open/close activity pair into the single gate it brackets.

    Matching is oldest-first among the opens that precede the close in history: a
    workflow that loops over the same gate id opens and closes it in order, so
    the *n*-th close belongs to the *n*-th open. Matching the newest instead
    would resolve a gate that is still waiting and leave the resolved one looking
    parked — the two vertices swap states, which is worse than not drawing them.
    """
    unmatched: dict[str, list[tuple[int, _Vertex]]] = {}
    vertices: list[_Vertex] = []

    for sid, sched in opens:
        gate_id = sched.gate_id or sched.activity_id
        outcome, opened_at, _ = r.terminal.get(sid) or ("", None, None)
        indexed = outcome == "completed"
        vertex = _Vertex(
            node=GraphNodeOut(
                id=f"gate:{sid}",
                label=gate_id,
                kind="gate",
                layer=0,
                state="waiting",
                started_at=opened_at or sched.scheduled_at,
                note=(
                    "Parked here, consuming nothing. The wait is a durable condition, "
                    "not a held thread — it survives worker restarts and resumes the "
                    "instant a decision is signalled."
                    if indexed
                    # Indexing is deliberately best-effort: a gate must not depend
                    # on a bookkeeping table being reachable. It still parks and
                    # still resolves; only its row in the inbox is missing.
                    else "This gate is waiting, but the write to the approval inbox "
                    "failed, so it will not appear in the inbox. Signalling it still works."
                ),
            ),
            layer_key=sched.decided_by,
        )
        vertices.append(vertex)
        unmatched.setdefault(gate_id, []).append((sid, vertex))

    for sched in closes:
        gate_id = sched.gate_id or ""
        queue = unmatched.get(gate_id) or []
        # Opens arrive in ascending event id, so the first one still unmatched is
        # the oldest — and it must actually precede this close.
        match = next((i for i, (sid, _) in enumerate(queue) if sid < sched.event_id), None)
        if match is None:
            continue
        _, vertex = queue.pop(match)
        decision = sched.decision or {}
        approved = decision.get("approved")
        timed_out = bool(decision.get("timed_out"))
        decided_by = decision.get("decided_by")
        comment = decision.get("comment")
        node = vertex.node
        node.ended_at = sched.scheduled_at
        node.duration_seconds = _duration(node.started_at, node.ended_at)
        node.approved = approved if isinstance(approved, bool) else None
        node.timed_out = timed_out
        node.decided_by = decided_by if isinstance(decided_by, str) and decided_by else None
        node.state = "completed"
        if timed_out:
            node.note = (
                "Nobody decided in time, so the gate expired and the workflow took "
                "its timeout branch. An expiry is a decision, not a failure."
            )
        elif node.approved is False:
            node.note = (
                f"Rejected{f' — {comment}' if isinstance(comment, str) and comment else ''}."
            )
        else:
            node.note = (
                f"Approved{f' — {comment}' if isinstance(comment, str) and comment else ''}."
            )

    # Anything left open either is the run's current parking spot, or was still
    # open when the run ended — which means it never got its decision.
    for queue in unmatched.values():
        for _, vertex in queue:
            if terminal_run:
                vertex.node.state = "canceled"
                vertex.node.note = (
                    "The run ended while this gate was still waiting, so no decision "
                    "was ever recorded against it."
                )
            elif r.open_timers:
                expiry = next((t for t in r.open_timers.values() if t is not None), None)
                if expiry is not None:
                    vertex.node.ended_at = expiry
                    vertex.node.note = (
                        (vertex.node.note or "")
                        + f" It expires at {expiry.isoformat()} if nobody decides."
                    ).strip()
    return vertices


def _wait_vertex(layer_key: int) -> _Vertex:
    """The durable wait a workflow is parked on when no gate activity marks it.

    Not every wait is bracketed by gate activities — a workflow may simply await
    a signal. History shows that as the absence of anything: a completed workflow
    task that scheduled no work and left nothing pending. Rendering that absence
    as a vertex is the difference between "parked, deliberately" and a DAG that
    looks like it stopped for no reason.
    """
    return _Vertex(
        node=GraphNodeOut(
            id="wait",
            label="durable wait",
            kind="wait",
            layer=0,
            state="waiting",
            note=(
                "The workflow is blocked on a condition — a signal, or a timer. It holds "
                "no worker and no thread while it waits, and resumes exactly here."
            ),
        ),
        layer_key=layer_key,
    )


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_graph(
    *,
    run_id: uuid.UUID,
    workflow_name: str,
    status: str,
    terminal: bool,
    events: Sequence[HistoryEvent],
    pending_activities: Sequence[PendingActivityInfo] = (),
    has_pending_workflow_task: bool = False,
    now: datetime | None = None,
) -> RunGraphOut:
    """Reconstruct this run's DAG, with each vertex's state as of ``now``."""
    now = now or datetime.now(UTC)
    r = _reduce(events)
    pending = {pa.activity_id: pa for pa in pending_activities}

    opens: list[tuple[int, _Sched]] = []
    closes: list[_Sched] = []
    vertices: list[_Vertex] = []

    for sid, sched in sorted(r.scheduled.items()):
        if sched.activity_type == GATE_OPEN:
            opens.append((sid, sched))
        elif sched.activity_type == GATE_CLOSE:
            closes.append(sched)
        else:
            vertices.append(_activity_vertex(sid, sched, r, pending))

    vertices.extend(_gate_vertices(opens, closes, r, terminal))

    # A gate already renders the parking spot; only synthesize a wait vertex when
    # the run is parked with nothing at all to point at.
    parked_at_gate = any(v.node.kind == "gate" and v.node.state == "waiting" for v in vertices)
    if (
        not (terminal or parked_at_gate or pending or has_pending_workflow_task)
        and r.parked
        and vertices
    ):
        vertices.append(_wait_vertex(max(v.layer_key for v in vertices) + 1))

    # Compact layer keys (raw event ids, and sparse once gates absorb their
    # bookkeeping activities) into consecutive ranks.
    ranks = {key: i for i, key in enumerate(sorted({v.layer_key for v in vertices}))}
    for v in vertices:
        v.node.layer = ranks[v.layer_key]

    nodes = sorted((v.node for v in vertices), key=lambda n: (n.layer, n.label))
    _disambiguate(nodes)

    by_layer: dict[int, list[GraphNodeOut]] = {}
    for n in nodes:
        by_layer.setdefault(n.layer, []).append(n)

    edges: list[GraphEdgeOut] = []
    ordered = sorted(by_layer)
    for a, b in zip(ordered, ordered[1:], strict=False):
        for src in by_layer[a]:
            for dst in by_layer[b]:
                edges.append(
                    GraphEdgeOut(source=src.id, target=dst.id, done=src.state == "completed")
                )

    return RunGraphOut(
        run_id=run_id,
        workflow_name=r.workflow_name or workflow_name,
        status=status,
        now=now,
        nodes=nodes,
        edges=edges,
        completed=sum(1 for n in nodes if n.state == "completed"),
        total=len(nodes),
    )


def _disambiguate(nodes: list[GraphNodeOut]) -> None:
    """Number repeated labels, so three sequential ``greet`` calls stay tellable apart.

    Only labels that actually collide are numbered: a workflow whose nodes have
    distinct names keeps them verbatim.
    """
    counts: dict[str, int] = {}
    for n in nodes:
        counts[n.label] = counts.get(n.label, 0) + 1
    seen: dict[str, int] = {}
    for n in nodes:
        if counts[n.label] < 2:
            continue
        seen[n.label] = seen.get(n.label, 0) + 1
        n.label = f"{n.label} #{seen[n.label]}"


__all__ = ["build_graph", "GATE_CLOSE", "GATE_OPEN"]
