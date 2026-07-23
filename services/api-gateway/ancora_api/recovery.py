"""Why a run pauses after a worker dies, and how it rebuilds itself.

A killed worker does not make a run fail — but it does make it *pause*, and the
pause is the part people misread. "Nothing is happening" and "it broke" look
identical from the outside. This module turns Temporal's own record into an
explanation of the pause: which attempt is stranded, which clock has to expire
before the server is allowed to call it dead, and which process picked the work
up afterwards.

Three different waits are possible after a kill, and they are not
interchangeable:

* **queued** — the task sits on a queue nobody is polling. Costs nothing and
  resolves the instant a worker comes back.
* **detecting** — an attempt was already *started* on a process that is now gone.
  The server cannot distinguish a dead worker from a slow one, so it waits out
  the contract that attempt was granted: ``start_to_close_timeout``, or the much
  shorter ``heartbeat_timeout`` if the node declares one. This is the wait that
  surprises people, and the only one whose length is a design decision.
* **backoff** — the attempt has already failed and the retry policy is holding
  the next one back.

The historical reconstruction is deliberately lossy. Temporal drops the
``ActivityTaskStarted`` event of an attempt that fails, so the *only* surviving
trace of a lost attempt is the ``attempt`` counter and ``last_failure`` carried
on the Started event of the attempt that eventually ran. That is enough to bound
the lost window — it cannot have begun before the activity was scheduled, and it
was over when the next attempt started — and it is reported as a bound
(``approximate``) rather than dressed up as a measurement.

Everything here is a pure function of (history, pending tasks, fleet liveness,
now), so the interesting cases are unit-testable without killing anything.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from google.protobuf.duration_pb2 import Duration
from google.protobuf.timestamp_pb2 import Timestamp
from temporalio.api.common.v1 import Payloads
from temporalio.api.enums.v1 import EventType, PendingActivityState, TimeoutType
from temporalio.api.history.v1 import HistoryEvent
from temporalio.api.workflow.v1 import PendingActivityInfo, PendingWorkflowTaskInfo

from ancora_api.schemas import (
    RecoveryMarkerOut,
    RecoverySpanOut,
    RecoveryWindowOut,
    RunRecoveryOut,
    WorkerOut,
)

# A schedule→start gap shorter than this is just scheduling latency, not a stall
# worth drawing. Anything longer means no worker was there to take the task.
QUEUE_GAP_FLOOR_SECONDS = 1.0

# How recently a worker must have checked in for "live" to mean anything here.
# Workers heartbeat every 5s behind a 20s Redis TTL, so for up to 20 seconds
# after a kill the registry still reports a dead process as live. That lag is
# comparable to the thing being explained, so a registry "live" is only believed
# when the underlying heartbeat is fresh; otherwise the answer is "unknown",
# which is the truth. Temporal is in exactly the same position — not knowing is
# precisely why the detection timeout exists.
LIVENESS_FRESH_SECONDS = 12.0

_TIMEOUT_LABEL = {
    TimeoutType.TIMEOUT_TYPE_START_TO_CLOSE: "start-to-close",
    TimeoutType.TIMEOUT_TYPE_SCHEDULE_TO_START: "schedule-to-start",
    TimeoutType.TIMEOUT_TYPE_SCHEDULE_TO_CLOSE: "schedule-to-close",
    TimeoutType.TIMEOUT_TYPE_HEARTBEAT: "heartbeat",
}


# --------------------------------------------------------------------------- #
# Fleet liveness
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FleetLiveness:
    """The worker registry reduced to the two questions this module asks.

    Identity matters at *process* granularity, not host: restarting a killed
    container reuses the hostname but gets a fresh pid, so a host-level check
    would cheerfully report the dead process as alive. ``replaced`` is the state
    that distinction buys — "the process holding this attempt is gone, and a
    different one is serving its queue now" — which is precisely the situation
    during the detection window.
    """

    # Registered as live *and* heard from recently enough to believe it.
    live_identities: frozenset[str] = frozenset()
    # Registered as live but past LIVENESS_FRESH_SECONDS — may already be dead.
    stale_identities: frozenset[str] = frozenset()
    known_hosts: frozenset[str] = frozenset()
    live_hosts: frozenset[str] = frozenset()
    live_queues: frozenset[str] = frozenset()

    @classmethod
    def from_workers(
        cls, workers: Iterable[WorkerOut], *, now: datetime | None = None
    ) -> FleetLiveness:
        now = now or datetime.now(UTC)
        live_identities: set[str] = set()
        stale_identities: set[str] = set()
        known_hosts: set[str] = set()
        live_hosts: set[str] = set()
        live_queues: set[str] = set()
        for w in workers:
            host = w.host or ""
            if host:
                known_hosts.add(host)
            if w.status != "live":
                continue
            if host:
                live_hosts.add(host)
            # Mirrors Temporal's default identity format, "<pid>@<host>".
            if w.pid is not None and host:
                identity = f"{w.pid}@{host}"
                beat = w.last_heartbeat_at
                fresh = beat is not None and (now - beat).total_seconds() <= LIVENESS_FRESH_SECONDS
                (live_identities if fresh else stale_identities).add(identity)
            live_queues.update(w.task_queues)
        return cls(
            live_identities=frozenset(live_identities),
            stale_identities=frozenset(stale_identities),
            known_hosts=frozenset(known_hosts),
            live_hosts=frozenset(live_hosts),
            live_queues=frozenset(live_queues),
        )

    def state_of(self, identity: str | None) -> str:
        """``live`` | ``replaced`` | ``gone`` | ``unknown`` for a worker identity."""
        if not identity:
            return "unknown"
        if identity in self.live_identities:
            return "live"
        if identity in self.stale_identities:
            # Registered, but its last check-in predates the kill we might be
            # explaining. Claiming either way here would be a guess.
            return "unknown"
        host = identity.rsplit("@", 1)[-1]
        if host in self.live_hosts:
            # Same container, different process: it was killed and came back.
            return "replaced"
        if host in self.known_hosts:
            return "gone"
        # Workflow workers do not register, so absence proves nothing about them.
        return "unknown"

    def queue_has_worker(self, queue: str | None) -> bool | None:
        if not queue or not self.live_queues:
            return None
        return queue in self.live_queues


# --------------------------------------------------------------------------- #
# Proto helpers
# --------------------------------------------------------------------------- #
def _dt(ts: Timestamp | None) -> datetime | None:
    """Proto timestamp → aware datetime, treating the zero value as unset."""
    if ts is None or (ts.seconds == 0 and ts.nanos == 0):
        return None
    return ts.ToDatetime().replace(tzinfo=UTC)


def _secs(d: Duration | None) -> float | None:
    if d is None:
        return None
    value = d.ToTimedelta().total_seconds()
    return value or None


def _node_id_from_input(payloads: Payloads | None) -> str | None:
    """Pull the node id out of a scheduled ``run_node`` activity's input.

    Temporal assigns activity ids by sequence — "1", "2", "3" — so the id alone
    labels the chart with numbers nobody can act on. The name the author gave the
    node ("search", "summarize-0") is in the activity's own input, which the
    default converter writes as plain JSON, so one decode recovers it.

    Best-effort by design: a custom converter, an encrypted payload, or a shape
    change all land in the fallback rather than breaking the view.
    """
    if payloads is None or not payloads.payloads:
        return None
    p = payloads.payloads[0]
    if p.metadata.get("encoding") != b"json/plain":
        return None
    try:
        decoded = json.loads(p.data)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(decoded, dict):
        node_id = decoded.get("node_id")
        if isinstance(node_id, str) and node_id:
            return node_id
    return None


def _node_id(activity_type: str, activity_id: str, decoded: str | None = None) -> str:
    """A label for the chart: the node's own name where one can be recovered.

    Falls back to the activity type (meaningful for the fixed-purpose activities
    like ``open_approval_gate``) and finally to the raw activity id.
    """
    if decoded:
        return decoded
    return activity_id if activity_type == "run_node" else activity_type


# --------------------------------------------------------------------------- #
# History reduction
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Scheduled:
    event_id: int
    activity_id: str
    activity_type: str
    task_queue: str
    scheduled_at: datetime | None
    start_to_close: float | None
    heartbeat: float | None
    maximum_attempts: int
    node_id: str


@dataclass
class _Reduction:
    scheduled: dict[int, _Scheduled]
    # scheduled_event_id → the (only persisted) started event, plus its attributes
    started: dict[int, tuple[datetime | None, str, int, str | None]]
    # scheduled_event_id → (outcome, ended_at, failure)
    terminal: dict[int, tuple[str, datetime | None, str | None]]
    markers: list[RecoveryMarkerOut]
    workers: list[str]
    replayed_activities: int
    handoffs: int


def _reduce_history(events: Sequence[HistoryEvent]) -> _Reduction:
    scheduled: dict[int, _Scheduled] = {}
    started: dict[int, tuple[datetime | None, str, int, str | None]] = {}
    terminal: dict[int, tuple[str, datetime | None, str | None]] = {}
    markers: list[RecoveryMarkerOut] = []
    workers: list[str] = []
    completed_activities = 0
    replayed_activities = 0
    handoffs = 0
    last_wf_identity: str | None = None

    def see_worker(identity: str) -> None:
        if identity and identity not in workers:
            workers.append(identity)

    for e in events:
        at = _dt(e.event_time)
        kind = e.event_type

        if kind == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            sch = e.activity_task_scheduled_event_attributes
            scheduled[e.event_id] = _Scheduled(
                event_id=e.event_id,
                activity_id=sch.activity_id,
                activity_type=sch.activity_type.name,
                task_queue=sch.task_queue.name,
                scheduled_at=at,
                start_to_close=_secs(sch.start_to_close_timeout),
                heartbeat=_secs(sch.heartbeat_timeout),
                maximum_attempts=int(sch.retry_policy.maximum_attempts),
                node_id=_node_id(
                    sch.activity_type.name,
                    sch.activity_id,
                    _node_id_from_input(sch.input),
                ),
            )

        elif kind == EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED:
            st = e.activity_task_started_event_attributes
            failure = st.last_failure.message if st.HasField("last_failure") else None
            started[st.scheduled_event_id] = (at, st.identity, int(st.attempt), failure or None)
            see_worker(st.identity)

        elif kind == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            done = e.activity_task_completed_event_attributes
            terminal[done.scheduled_event_id] = ("completed", at, None)
            completed_activities += 1

        elif kind == EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
            bad = e.activity_task_failed_event_attributes
            terminal[bad.scheduled_event_id] = ("failed", at, bad.failure.message or None)

        elif kind == EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT:
            late = e.activity_task_timed_out_event_attributes
            terminal[late.scheduled_event_id] = ("timed_out", at, late.failure.message or None)

        elif kind == EventType.EVENT_TYPE_ACTIVITY_TASK_CANCELED:
            cancelled = e.activity_task_canceled_event_attributes
            terminal[cancelled.scheduled_event_id] = ("canceled", at, None)

        elif kind == EventType.EVENT_TYPE_WORKFLOW_TASK_STARTED:
            identity = e.workflow_task_started_event_attributes.identity
            see_worker(identity)
            # Only an identity *change* is evidence of a handoff. Every workflow
            # task is nominally a replay, but a worker holding a sticky cache
            # continues rather than rebuilding — reporting each one as a replay
            # would inflate the number that matters.
            if identity and last_wf_identity is not None and identity != last_wf_identity:
                handoffs += 1
                replayed_activities = max(replayed_activities, completed_activities)
                markers.append(
                    RecoveryMarkerOut(
                        at=at or datetime.now(UTC),
                        kind="worker_changed",
                        label=identity,
                        detail=(
                            f"picked up from {last_wf_identity}; rebuilt state from "
                            f"{completed_activities} recorded "
                            f"{'result' if completed_activities == 1 else 'results'}"
                        ),
                    )
                )
            if identity:
                last_wf_identity = identity

        elif kind == EventType.EVENT_TYPE_WORKFLOW_TASK_TIMED_OUT:
            wt = e.workflow_task_timed_out_event_attributes
            markers.append(
                RecoveryMarkerOut(
                    at=at or datetime.now(UTC),
                    kind="workflow_task_timeout",
                    label="workflow task timed out",
                    detail=(
                        f"{_TIMEOUT_LABEL.get(wt.timeout_type, 'timeout')} — the worker "
                        "holding the orchestration step stopped answering"
                    ),
                )
            )

    return _Reduction(
        scheduled=scheduled,
        started=started,
        terminal=terminal,
        markers=markers,
        workers=workers,
        replayed_activities=replayed_activities,
        handoffs=handoffs,
    )


def _spans(
    reduction: _Reduction,
    pending_activities: Sequence[PendingActivityInfo] = (),
) -> list[RecoverySpanOut]:
    """Place every attempt we can account for on the time axis.

    Pending state is folded in because history alone under-reports the present:
    Temporal writes ``ActivityTaskStarted`` **lazily**, only once the attempt
    reaches a terminal event. An attempt in flight right now — including the one
    stranded on a worker that just died — is therefore invisible in history and
    would otherwise be drawn as if it were still sitting on the queue.
    """
    # Keyed by activity id: what the server says is happening this instant.
    in_flight = {
        pa.activity_id: pa
        for pa in pending_activities
        if pa.state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED
    }
    spans: list[RecoverySpanOut] = []

    for sid, sched in sorted(reduction.scheduled.items()):
        node_id = sched.node_id
        start_info = reduction.started.get(sid)
        term = reduction.terminal.get(sid)

        if start_info is None:
            pending = in_flight.get(sched.activity_id)
            if pending is not None and term is None:
                started_now = _dt(pending.last_started_time)
                attempt_now = int(pending.attempt) or 1
                if attempt_now > 1 and started_now is not None:
                    spans.append(
                        RecoverySpanOut(
                            activity_id=sched.activity_id,
                            node_id=node_id,
                            activity_type=sched.activity_type,
                            attempt=attempt_now - 1,
                            worker=None,
                            outcome="lost",
                            started_at=sched.scheduled_at,
                            ended_at=started_now,
                            failure=(
                                pending.last_failure.message
                                if pending.HasField("last_failure")
                                else None
                            ),
                            lost_attempts=attempt_now - 1,
                            approximate=True,
                        )
                    )
                elif (
                    sched.scheduled_at is not None
                    and started_now is not None
                    and (started_now - sched.scheduled_at).total_seconds() > QUEUE_GAP_FLOOR_SECONDS
                ):
                    spans.append(
                        RecoverySpanOut(
                            activity_id=sched.activity_id,
                            node_id=node_id,
                            activity_type=sched.activity_type,
                            attempt=1,
                            worker=None,
                            outcome="queued",
                            started_at=sched.scheduled_at,
                            ended_at=started_now,
                        )
                    )
                spans.append(
                    RecoverySpanOut(
                        activity_id=sched.activity_id,
                        node_id=node_id,
                        activity_type=sched.activity_type,
                        attempt=attempt_now,
                        worker=pending.last_worker_identity or None,
                        outcome="running",
                        started_at=started_now or sched.scheduled_at,
                        ended_at=None,
                    )
                )
                continue

            # Scheduled and never started: nobody has taken it off the queue.
            outcome, ended_at, failure = term or ("queued", None, None)
            spans.append(
                RecoverySpanOut(
                    activity_id=sched.activity_id,
                    node_id=node_id,
                    activity_type=sched.activity_type,
                    attempt=1,
                    worker=None,
                    outcome=outcome,
                    started_at=sched.scheduled_at,
                    ended_at=ended_at,
                    failure=failure,
                    approximate=outcome != "queued",
                )
            )
            continue

        started_at, identity, attempt, last_failure = start_info

        if attempt > 1:
            # The attempts that died leave no start event behind. Bound them:
            # they began no earlier than the schedule and were over by this start.
            spans.append(
                RecoverySpanOut(
                    activity_id=sched.activity_id,
                    node_id=node_id,
                    activity_type=sched.activity_type,
                    attempt=attempt - 1,
                    worker=None,
                    outcome="lost",
                    started_at=sched.scheduled_at,
                    ended_at=started_at,
                    failure=last_failure,
                    lost_attempts=attempt - 1,
                    approximate=True,
                )
            )
        elif (
            sched.scheduled_at is not None
            and started_at is not None
            and (started_at - sched.scheduled_at).total_seconds() > QUEUE_GAP_FLOOR_SECONDS
        ):
            # Never retried, but it waited: no worker was polling that queue.
            spans.append(
                RecoverySpanOut(
                    activity_id=sched.activity_id,
                    node_id=node_id,
                    activity_type=sched.activity_type,
                    attempt=1,
                    worker=None,
                    outcome="queued",
                    started_at=sched.scheduled_at,
                    ended_at=started_at,
                )
            )

        outcome, ended_at, failure = term or ("running", None, None)
        spans.append(
            RecoverySpanOut(
                activity_id=sched.activity_id,
                node_id=node_id,
                activity_type=sched.activity_type,
                attempt=attempt,
                worker=identity or None,
                outcome=outcome,
                started_at=started_at,
                ended_at=ended_at,
                failure=failure,
            )
        )

    return spans


# --------------------------------------------------------------------------- #
# Live windows — the clock the run is waiting on right now
# --------------------------------------------------------------------------- #
def _activity_window(
    pa: PendingActivityInfo,
    sched: _Scheduled | None,
    liveness: FleetLiveness,
    now: datetime,
    killed_at: Sequence[datetime] = (),
) -> RecoveryWindowOut | None:
    activity_type = pa.activity_type.name
    # Pending state carries no input, so the name comes from the schedule event.
    node_id = sched.node_id if sched else _node_id(activity_type, pa.activity_id)
    queue = sched.task_queue if sched else None
    has_worker = liveness.queue_has_worker(queue)
    attempt = int(pa.attempt)
    identity = pa.last_worker_identity or None
    worker_state = liveness.state_of(identity)

    # Under Compose a restarted container comes back on the same hostname with
    # the same pid, so Temporal's "<pid>@<host>" identity is byte-identical to
    # the process that just died — and the registry, re-registering under the
    # same worker id, reports it live. Identity alone therefore cannot tell the
    # replacement from its predecessor. A kill through the Chaos Lab is direct
    # evidence that it *is* a different process: if a worker was killed after
    # this attempt started, whoever holds it is gone regardless of what the
    # registry says about the name.
    attempt_started = _dt(pa.last_started_time)
    if (
        worker_state == "live"
        and attempt_started is not None
        and any(attempt_started < k <= now for k in killed_at)
    ):
        worker_state = "replaced"

    def window(
        kind: str,
        *,
        clock: str | None = None,
        started_at: datetime | None = None,
        deadline_at: datetime | None = None,
        timeout_seconds: float | None = None,
        elapsed_seconds: float = 0.0,
        remaining_seconds: float | None = None,
        heartbeat_at: datetime | None = None,
        heartbeat_timeout_seconds: float | None = None,
        reason: str = "",
    ) -> RecoveryWindowOut:
        """Fill in the identity fields every window shares."""
        return RecoveryWindowOut(
            activity_id=pa.activity_id,
            node_id=node_id,
            attempt=attempt,
            worker=identity,
            worker_state=worker_state,
            queue=queue,
            queue_has_worker=has_worker,
            kind=kind,
            clock=clock,
            started_at=started_at,
            deadline_at=deadline_at,
            timeout_seconds=timeout_seconds,
            elapsed_seconds=elapsed_seconds,
            remaining_seconds=remaining_seconds,
            heartbeat_at=heartbeat_at,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            reason=reason,
        )

    if pa.state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED:
        started_at = _dt(pa.last_started_time)
        if started_at is None:
            return None
        stc = sched.start_to_close if sched else None
        hb_timeout = sched.heartbeat if sched else None
        heartbeat_at = _dt(pa.last_heartbeat_time)

        clock = "start_to_close"
        timeout = stc
        deadline = started_at + timedelta(seconds=stc) if stc else None
        if hb_timeout:
            hb_from = heartbeat_at or started_at
            hb_deadline = hb_from + timedelta(seconds=hb_timeout)
            if deadline is None or hb_deadline < deadline:
                clock, timeout, deadline = "heartbeat", hb_timeout, hb_deadline

        remaining = (deadline - now).total_seconds() if deadline else None
        if worker_state in ("gone", "replaced"):
            reason = (
                f"Attempt {attempt} is held by {identity}, a process that no longer exists. "
                "Temporal cannot tell a dead worker from a slow one, so it will not "
                "reassign the work until the timeout that attempt was granted expires."
            )
        elif worker_state == "live":
            reason = f"Attempt {attempt} is running on {identity}. This is work, not a stall."
        else:
            # The honest case, and the common one immediately after a kill: the
            # registry's own heartbeat is not fresh enough to rule either way.
            # Saying so is better than guessing — and it is the exact ambiguity
            # the timeout below exists to resolve.
            reason = (
                f"Attempt {attempt} is held by {identity or 'a worker'} that has not "
                "checked in recently enough to confirm it is alive. That ambiguity is "
                "the whole reason for the timeout: rather than guess, the server waits "
                "it out and only then reassigns the work."
            )
        if hb_timeout and clock == "heartbeat":
            reason += (
                f" It heartbeats every {hb_timeout:g}s, which is what shortens the wait "
                "from the full start-to-close budget."
            )
        elif stc and not hb_timeout:
            reason += (
                f" This node declares no heartbeat, so the full {stc:g}s start-to-close "
                "budget has to elapse first."
            )

        return window(
            "detecting",
            clock=clock,
            started_at=started_at,
            deadline_at=deadline,
            timeout_seconds=timeout,
            elapsed_seconds=max(0.0, (now - started_at).total_seconds()),
            remaining_seconds=remaining,
            heartbeat_at=heartbeat_at,
            heartbeat_timeout_seconds=hb_timeout,
            reason=reason,
        )

    if pa.state == PendingActivityState.PENDING_ACTIVITY_STATE_SCHEDULED:
        next_at = _dt(pa.next_attempt_schedule_time)
        scheduled_at = _dt(pa.scheduled_time)
        if next_at is not None and next_at > now:
            interval = _secs(pa.current_retry_interval)
            failure = pa.last_failure.message if pa.HasField("last_failure") else None
            return window(
                "backoff",
                clock="retry_backoff",
                started_at=_dt(pa.last_attempt_complete_time) or scheduled_at,
                deadline_at=next_at,
                timeout_seconds=interval,
                elapsed_seconds=max(
                    0.0,
                    (now - (_dt(pa.last_attempt_complete_time) or next_at)).total_seconds(),
                ),
                remaining_seconds=(next_at - now).total_seconds(),
                reason=(
                    f"Attempt {attempt} failed"
                    + (f" ({failure})" if failure else "")
                    + " and the retry policy is holding the next one back. The wait is "
                    "the backoff interval, not a detection delay."
                ),
            )
        return window(
            "queued",
            started_at=scheduled_at,
            elapsed_seconds=max(0.0, (now - scheduled_at).total_seconds()) if scheduled_at else 0.0,
            reason=(
                f"Waiting on the {queue} queue with no live worker polling it. "
                "This costs nothing and clears the moment a worker comes back — "
                "there is no timeout to wait out."
                if has_worker is False
                else f"Queued on {queue}, waiting for a worker to pick it up."
            ),
        )

    return None


def _workflow_window(
    task: PendingWorkflowTaskInfo,
    queue: str | None,
    timeout_seconds: float | None,
    liveness: FleetLiveness,
    now: datetime,
) -> RecoveryWindowOut | None:
    """The orchestration step itself can be the thing that is stuck."""
    scheduled_at = _dt(task.scheduled_time)
    started_at = _dt(task.started_time)
    attempt = int(task.attempt) or 1

    if started_at is not None:
        deadline = started_at + timedelta(seconds=timeout_seconds) if timeout_seconds else None
        return RecoveryWindowOut(
            activity_id="workflow-task",
            node_id="workflow",
            kind="workflow_task",
            clock="start_to_close",
            attempt=attempt,
            queue=queue,
            queue_has_worker=liveness.queue_has_worker(queue),
            started_at=started_at,
            deadline_at=deadline,
            timeout_seconds=timeout_seconds,
            elapsed_seconds=max(0.0, (now - started_at).total_seconds()),
            remaining_seconds=(deadline - now).total_seconds() if deadline else None,
            reason=(
                "A workflow worker took the orchestration step and has not answered. "
                "Workflow task timeouts are short by design, so this resolves quickly "
                "— then another worker replays history and continues."
            ),
        )

    if scheduled_at is not None and (now - scheduled_at).total_seconds() > QUEUE_GAP_FLOOR_SECONDS:
        return RecoveryWindowOut(
            activity_id="workflow-task",
            node_id="workflow",
            kind="queued",
            attempt=attempt,
            queue=queue,
            queue_has_worker=liveness.queue_has_worker(queue),
            started_at=scheduled_at,
            elapsed_seconds=(now - scheduled_at).total_seconds(),
            reason=(
                "The orchestration step is queued with no workflow worker to run it. "
                "Nothing is lost; it starts the moment one returns."
            ),
        )
    return None


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_recovery(
    *,
    run_id: uuid.UUID,
    status: str,
    events: Sequence[HistoryEvent],
    pending_activities: Sequence[PendingActivityInfo] = (),
    pending_workflow_task: PendingWorkflowTaskInfo | None = None,
    workflow_task_queue: str | None = None,
    workflow_task_timeout: float | None = None,
    chaos_events: Sequence[dict[str, object]] = (),
    liveness: FleetLiveness | None = None,
    now: datetime | None = None,
) -> RunRecoveryOut:
    """Assemble the recovery view from Temporal's record plus fleet liveness."""
    now = now or datetime.now(UTC)
    liveness = liveness or FleetLiveness()

    reduction = _reduce_history(events)
    spans = _spans(reduction, pending_activities)
    markers = list(reduction.markers)

    # Injections are process-wide rather than run-scoped, but on the timeline
    # they are the cause the other markers are effects of.
    for ev in chaos_events:
        at = ev.get("at")
        if not isinstance(at, int | float):
            continue
        action = str(ev.get("action", "?"))
        markers.append(
            RecoveryMarkerOut(
                at=datetime.fromtimestamp(float(at), UTC),
                kind=action,
                label=f"{action} {ev.get('service', '')}".strip(),
                detail=str(ev.get("detail") or "") or None,
            )
        )
    markers.sort(key=lambda m: m.at)

    killed_at = [m.at for m in markers if m.kind == "kill"]

    windows: list[RecoveryWindowOut] = []
    by_activity_id = {s.activity_id: s for s in reduction.scheduled.values()}
    for pa in pending_activities:
        window = _activity_window(pa, by_activity_id.get(pa.activity_id), liveness, now, killed_at)
        if window is not None:
            windows.append(window)
    if pending_workflow_task is not None:
        window = _workflow_window(
            pending_workflow_task, workflow_task_queue, workflow_task_timeout, liveness, now
        )
        if window is not None:
            windows.append(window)

    # Longest wait first: that is the one holding the run up.
    windows.sort(key=lambda w: w.elapsed_seconds, reverse=True)

    return RunRecoveryOut(
        run_id=run_id,
        status=status,
        now=now,
        workers=reduction.workers,
        spans=spans,
        markers=markers,
        windows=windows,
        replayed_activities=reduction.replayed_activities,
        handoffs=reduction.handoffs,
    )
