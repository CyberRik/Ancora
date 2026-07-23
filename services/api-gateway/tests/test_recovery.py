"""Reconstructing a worker death from Temporal's record.

The recovery view makes claims a visitor is expected to believe — "this attempt
is stranded on a process that no longer exists", "nothing will move for another
four minutes", "the replacement rebuilt state from six recorded results without
re-running them". Each of those is a reading of history that can be wrong in a
way that is invisible on screen, so they are pinned here against synthetic
histories rather than against a live kill.

The three waits are the point. A run that is queued, a run that is waiting out a
detection timeout, and a run in retry backoff all look identical from outside —
"nothing is happening" — and only one of them is a design decision worth
explaining. Conflating them is the failure mode these tests exist to catch.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from google.protobuf.duration_pb2 import Duration
from google.protobuf.timestamp_pb2 import Timestamp
from temporalio.api.enums.v1 import EventType, PendingActivityState, TimeoutType
from temporalio.api.history.v1 import HistoryEvent
from temporalio.api.workflow.v1 import PendingActivityInfo, PendingWorkflowTaskInfo

from ancora_api.recovery import FleetLiveness, build_recovery
from ancora_api.schemas import RunRecoveryOut, WorkerOut

RUN_ID = __import__("uuid").UUID("00000000-0000-0000-0000-0000000000ab")
T0 = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def ts(offset_seconds: float) -> Timestamp:
    stamp = Timestamp()
    stamp.FromDatetime(T0 + timedelta(seconds=offset_seconds))
    return stamp


def secs(value: float) -> Duration:
    d = Duration()
    d.FromTimedelta(timedelta(seconds=value))
    return d


_next_id = iter(range(1, 10_000))


def scheduled(
    activity_id: str,
    *,
    at: float,
    queue: str = "ancora-cpu",
    start_to_close: float = 300.0,
    heartbeat: float | None = None,
    activity_type: str = "run_node",
    node_id: str | None = None,
    encoding: bytes = b"json/plain",
) -> HistoryEvent:
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    a = e.activity_task_scheduled_event_attributes
    a.activity_id = activity_id
    a.activity_type.name = activity_type
    a.task_queue.name = queue
    a.start_to_close_timeout.CopyFrom(secs(start_to_close))
    if heartbeat:
        a.heartbeat_timeout.CopyFrom(secs(heartbeat))
    a.retry_policy.maximum_attempts = 6
    if node_id is not None:
        p = a.input.payloads.add()
        p.metadata["encoding"] = encoding
        p.data = json.dumps({"type_name": "llm", "node_id": node_id}).encode()
    return e


def started(
    sched: HistoryEvent, *, at: float, identity: str, attempt: int = 1, last_failure: str = ""
) -> HistoryEvent:
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED
    a = e.activity_task_started_event_attributes
    a.scheduled_event_id = sched.event_id
    a.identity = identity
    a.attempt = attempt
    if last_failure:
        a.last_failure.message = last_failure
    return e


def completed(sched: HistoryEvent, *, at: float) -> HistoryEvent:
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
    e.activity_task_completed_event_attributes.scheduled_event_id = sched.event_id
    return e


def wf_task_started(*, at: float, identity: str) -> HistoryEvent:
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_WORKFLOW_TASK_STARTED
    e.workflow_task_started_event_attributes.identity = identity
    return e


def wf_task_timed_out(*, at: float) -> HistoryEvent:
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_WORKFLOW_TASK_TIMED_OUT
    e.workflow_task_timed_out_event_attributes.timeout_type = (
        TimeoutType.TIMEOUT_TYPE_START_TO_CLOSE
    )
    return e


def pending_started(
    activity_id: str,
    *,
    started_at: float,
    identity: str,
    attempt: int = 1,
    heartbeat_at: float | None = None,
    activity_type: str = "run_node",
) -> PendingActivityInfo:
    pa = PendingActivityInfo(activity_id=activity_id, attempt=attempt)
    pa.activity_type.name = activity_type
    pa.state = PendingActivityState.PENDING_ACTIVITY_STATE_STARTED
    pa.last_started_time.CopyFrom(ts(started_at))
    pa.last_worker_identity = identity
    if heartbeat_at is not None:
        pa.last_heartbeat_time.CopyFrom(ts(heartbeat_at))
    return pa


def pending_scheduled(
    activity_id: str,
    *,
    scheduled_at: float,
    attempt: int = 1,
    next_attempt_at: float | None = None,
    retry_interval: float | None = None,
    last_failure: str = "",
    identity: str = "",
) -> PendingActivityInfo:
    pa = PendingActivityInfo(activity_id=activity_id, attempt=attempt)
    pa.activity_type.name = "run_node"
    pa.state = PendingActivityState.PENDING_ACTIVITY_STATE_SCHEDULED
    pa.scheduled_time.CopyFrom(ts(scheduled_at))
    if identity:
        pa.last_worker_identity = identity
    if next_attempt_at is not None:
        pa.next_attempt_schedule_time.CopyFrom(ts(next_attempt_at))
    if retry_interval is not None:
        pa.current_retry_interval.CopyFrom(secs(retry_interval))
    if last_failure:
        pa.last_failure.message = last_failure
    return pa


def worker(
    identity: str,
    *,
    status: str = "live",
    queues: list[str] | None = None,
    heartbeat_at: float = 0.0,
) -> WorkerOut:
    pid_str, host = identity.split("@", 1)
    return WorkerOut(
        worker_id=f"aw-{host}",
        host=host,
        pid=int(pid_str),
        pools=["cpu"],
        task_queues=queues or ["ancora-cpu"],
        resources={},
        status=status,
        registered_at=T0,
        last_heartbeat_at=T0 + timedelta(seconds=heartbeat_at),
    )


def fleet(*workers: WorkerOut, at: float = 0.0) -> FleetLiveness:
    """Liveness as of a fixed instant — never the wall clock, or heartbeat
    freshness would depend on when the suite happens to run."""
    return FleetLiveness.from_workers(workers, now=T0 + timedelta(seconds=at))


def build(
    events: list[HistoryEvent],
    *,
    pending_activities: Sequence[PendingActivityInfo] = (),
    pending_workflow_task: PendingWorkflowTaskInfo | None = None,
    workflow_task_queue: str | None = None,
    workflow_task_timeout: float | None = None,
    chaos_events: Sequence[dict[str, object]] = (),
    liveness: FleetLiveness | None = None,
    now: datetime | None = None,
) -> RunRecoveryOut:
    return build_recovery(
        run_id=RUN_ID,
        status="Running",
        events=events,
        pending_activities=pending_activities,
        pending_workflow_task=pending_workflow_task,
        workflow_task_queue=workflow_task_queue,
        workflow_task_timeout=workflow_task_timeout,
        chaos_events=chaos_events,
        liveness=liveness,
        now=now,
    )


# --------------------------------------------------------------------------- #
# Labelling — a chart of "1, 2, 3" explains nothing
# --------------------------------------------------------------------------- #
def test_rows_are_labelled_with_the_node_name_not_the_activity_sequence() -> None:
    # Temporal numbers activities by sequence, so the id alone would label every
    # row with an integer. The name the workflow author chose is in the input.
    s1 = scheduled("1", at=0, node_id="summarize-0")
    result = build([s1, started(s1, at=1, identity="7@host-a"), completed(s1, at=2)])
    assert [s.node_id for s in result.spans] == ["summarize-0"]
    # The activity id is still carried, so the row can be traced back to history.
    assert result.spans[0].activity_id == "1"


def test_an_undecodable_input_falls_back_instead_of_breaking_the_view() -> None:
    # A custom or encrypted converter must cost the label, not the chart.
    s1 = scheduled("1", at=0, node_id="search", encoding=b"binary/encrypted")
    result = build([s1, started(s1, at=1, identity="7@host-a"), completed(s1, at=2)])
    assert result.spans[0].node_id == "1"


def test_a_fixed_purpose_activity_is_labelled_by_its_type() -> None:
    s1 = scheduled("9", at=0, activity_type="open_approval_gate")
    result = build([s1, started(s1, at=1, identity="7@host-a"), completed(s1, at=2)])
    assert result.spans[0].node_id == "open_approval_gate"


# --------------------------------------------------------------------------- #
# Worker identity — the difference between "restarted" and "still alive"
# --------------------------------------------------------------------------- #
def test_a_restarted_container_is_not_the_same_worker() -> None:
    # Same host, new pid: the process that held the attempt is gone even though
    # the hostname is back. A host-level check would call this "live" and the UI
    # would claim the run is progressing while it waits out a timeout.
    live = fleet(worker("42@abc123"))
    assert live.state_of("42@abc123") == "live"
    assert live.state_of("7@abc123") == "replaced"


def test_a_host_that_never_came_back_is_gone() -> None:
    live = fleet(worker("42@abc123", status="stale"))
    assert live.state_of("9@abc123") == "gone"


def test_a_registration_that_has_gone_quiet_is_not_believed() -> None:
    # Workers heartbeat every 5s behind a 20s Redis TTL, so for up to 20 seconds
    # after a kill the registry still cheerfully reports the dead process as
    # live. That lag is the same order as the delay being explained, so a "live"
    # whose heartbeat predates the window must degrade to "unknown" rather than
    # let the UI announce that a killed worker is busy working.
    w = worker("42@abc123", heartbeat_at=0)
    assert fleet(w, at=4).state_of("42@abc123") == "live"
    assert fleet(w, at=18).state_of("42@abc123") == "unknown"


def test_an_unregistered_worker_is_unknown_not_dead() -> None:
    # Workflow workers do not register, so their absence proves nothing. Calling
    # them dead would put a false "crashed" marker on every healthy run.
    live = fleet(worker("42@abc123"))
    assert live.state_of("1@some-other-host") == "unknown"
    assert live.state_of(None) == "unknown"


# --------------------------------------------------------------------------- #
# Handoffs and replay counting
# --------------------------------------------------------------------------- #
def test_the_same_worker_polling_repeatedly_is_not_a_handoff() -> None:
    # A healthy run emits many workflow tasks on one worker. Counting each as a
    # replay would report a recovery that never happened.
    s1 = scheduled("n1", at=0)
    events = [
        wf_task_started(at=0, identity="7@host-a"),
        s1,
        started(s1, at=1, identity="7@host-a"),
        completed(s1, at=2),
        wf_task_started(at=3, identity="7@host-a"),
        wf_task_started(at=4, identity="7@host-a"),
    ]
    result = build(events)
    assert result.handoffs == 0
    assert result.replayed_activities == 0
    assert [m.kind for m in result.markers] == []


def test_a_new_worker_taking_over_reports_what_it_rebuilt() -> None:
    s1, s2 = scheduled("n1", at=0), scheduled("n2", at=3)
    events = [
        wf_task_started(at=0, identity="7@host-a"),
        s1,
        started(s1, at=1, identity="7@host-a"),
        completed(s1, at=2),
        s2,
        started(s2, at=4, identity="7@host-a"),
        completed(s2, at=5),
        # host-a dies here; host-b picks the workflow up.
        wf_task_started(at=30, identity="7@host-b"),
    ]
    result = build(events)
    assert result.handoffs == 1
    # Two activity results already in history — replayed, not re-executed.
    assert result.replayed_activities == 2
    handoff = next(m for m in result.markers if m.kind == "worker_changed")
    assert "7@host-a" in (handoff.detail or "")
    assert "2 recorded results" in (handoff.detail or "")
    assert result.workers == ["7@host-a", "7@host-b"]


def test_a_workflow_task_timeout_is_marked_as_a_crash() -> None:
    events = [wf_task_started(at=0, identity="7@host-a"), wf_task_timed_out(at=10)]
    result = build(events)
    kinds = [m.kind for m in result.markers]
    assert "workflow_task_timeout" in kinds


# --------------------------------------------------------------------------- #
# Lost attempts — the span Temporal never writes down
# --------------------------------------------------------------------------- #
def test_an_attempt_that_died_with_its_worker_is_reconstructed_as_a_bound() -> None:
    # Temporal drops the Started event of an attempt that fails, so attempt 2's
    # Started event is the *only* evidence attempt 1 ever existed. The gap must
    # still appear on the timeline — it is the whole delay being explained.
    s1 = scheduled("n1", at=0, start_to_close=300)
    events = [
        s1,
        started(s1, at=302, identity="7@host-b", attempt=2, last_failure="activity StartToClose"),
        completed(s1, at=310),
    ]
    result = build(events)
    lost = [s for s in result.spans if s.outcome == "lost"]
    assert len(lost) == 1
    span = lost[0]
    assert span.attempt == 1
    assert span.worker is None  # the process died before the server recorded it
    assert span.approximate is True
    # Bounded by the schedule on one side and the surviving attempt on the other.
    assert span.started_at == T0
    assert span.ended_at == T0 + timedelta(seconds=302)
    assert "StartToClose" in (span.failure or "")

    survivor = next(s for s in result.spans if s.outcome == "completed")
    assert survivor.attempt == 2
    assert survivor.worker == "7@host-b"
    assert survivor.approximate is False


def test_a_long_wait_before_the_first_attempt_is_shown_as_queue_time() -> None:
    # No retry happened, but the task sat unclaimed — a worker was down. Without
    # this the timeline would silently swallow the gap.
    s1 = scheduled("n1", at=0)
    events = [s1, started(s1, at=45, identity="7@host-b"), completed(s1, at=46)]
    result = build(events)
    queued = [s for s in result.spans if s.outcome == "queued"]
    assert len(queued) == 1
    assert queued[0].ended_at == T0 + timedelta(seconds=45)


def test_ordinary_scheduling_latency_is_not_drawn_as_a_stall() -> None:
    s1 = scheduled("n1", at=0)
    events = [s1, started(s1, at=0.2, identity="7@host-a"), completed(s1, at=1)]
    result = build(events)
    assert [s.outcome for s in result.spans] == ["completed"]


def test_an_in_flight_attempt_is_drawn_even_though_history_omits_it() -> None:
    # Temporal writes ActivityTaskStarted *lazily* — only when the attempt
    # reaches a terminal event. So the attempt running right now, including the
    # one stranded on a worker that just died, does not appear in history at all.
    # Reading history alone would draw it as still sitting on the queue, which
    # is exactly backwards: it is the bar the whole delay belongs to.
    s1 = scheduled("n1", at=0)
    result = build(
        [s1],  # no Started event, though one is plainly running
        pending_activities=[pending_started("n1", started_at=2, identity="7@host-a")],
        now=T0 + timedelta(seconds=30),
    )
    running = [s for s in result.spans if s.outcome == "running"]
    assert len(running) == 1
    assert running[0].worker == "7@host-a"
    assert running[0].started_at == T0 + timedelta(seconds=2)
    # It must not also be reported as still queued.
    assert [s.outcome for s in result.spans] == ["queued", "running"]


def test_a_retry_in_flight_still_shows_the_attempt_that_was_lost() -> None:
    # Same lazy-write problem, one attempt later: history has nothing at all,
    # and the only evidence attempt 1 existed is the pending attempt counter.
    s1 = scheduled("n1", at=0)
    result = build(
        [s1],
        pending_activities=[pending_started("n1", started_at=300, identity="7@host-b", attempt=2)],
        now=T0 + timedelta(seconds=320),
    )
    lost = [s for s in result.spans if s.outcome == "lost"]
    assert len(lost) == 1
    assert lost[0].attempt == 1
    assert lost[0].approximate is True
    assert lost[0].ended_at == T0 + timedelta(seconds=300)
    assert [s.outcome for s in result.spans if s.outcome == "running"] == ["running"]


def test_a_node_still_running_has_no_end() -> None:
    s1 = scheduled("n1", at=0)
    result = build([s1, started(s1, at=1, identity="7@host-a")])
    span = result.spans[0]
    assert span.outcome == "running"
    assert span.ended_at is None


# --------------------------------------------------------------------------- #
# The live window — which clock is the run waiting on, and why
# --------------------------------------------------------------------------- #
def test_a_stranded_attempt_reports_the_detection_deadline() -> None:
    s1 = scheduled("n1", at=0, start_to_close=300)
    result = build(
        [s1, started(s1, at=0, identity="7@host-a")],
        pending_activities=[pending_started("n1", started_at=0, identity="7@host-a")],
        # host-a came back with a new pid — the classic post-kill state.
        liveness=fleet(worker("99@host-a"), at=60),
        now=T0 + timedelta(seconds=60),
    )
    window = result.windows[0]
    assert window.kind == "detecting"
    assert window.clock == "start_to_close"
    assert window.worker_state == "replaced"
    assert window.elapsed_seconds == 60
    assert window.remaining_seconds == 240
    assert window.deadline_at == T0 + timedelta(seconds=300)
    assert "no longer exists" in window.reason
    assert "no heartbeat" in window.reason


def test_a_heartbeat_shortens_the_detection_window() -> None:
    # This is the actionable half of the explanation: the wait is not inherent,
    # it is whatever the node's heartbeat contract says.
    s1 = scheduled("n1", at=0, start_to_close=600, heartbeat=30)
    result = build(
        [s1, started(s1, at=0, identity="7@host-a")],
        pending_activities=[
            pending_started("n1", started_at=0, identity="7@host-a", heartbeat_at=50)
        ],
        liveness=fleet(worker("99@host-a"), at=60),
        now=T0 + timedelta(seconds=60),
    )
    window = result.windows[0]
    assert window.clock == "heartbeat"
    assert window.timeout_seconds == 30
    # 30s from the last heartbeat (t=50), not 600s from the start.
    assert window.deadline_at == T0 + timedelta(seconds=80)
    assert window.remaining_seconds == 20
    assert "heartbeats every 30s" in window.reason


def test_a_kill_outranks_an_identity_that_merely_looks_alive() -> None:
    # Under Compose a restarted container returns on the same hostname with the
    # same pid, so Temporal's identity string for the replacement is byte-for-byte
    # the predecessor's, and the registry re-registers it as live. Identity alone
    # would announce "this is work, not a stall" about an attempt stranded on a
    # process that no longer exists. A Chaos Lab kill is direct evidence otherwise.
    s1 = scheduled("n1", at=0, node_id="search")
    result = build(
        [s1, started(s1, at=0, identity="7@host-a")],
        pending_activities=[pending_started("n1", started_at=0, identity="7@host-a")],
        liveness=fleet(worker("7@host-a", heartbeat_at=58), at=60),
        chaos_events=[
            {
                "action": "kill",
                "service": "activity-worker",
                "at": (T0 + timedelta(seconds=20)).timestamp(),
            }
        ],
        now=T0 + timedelta(seconds=60),
    )
    window = result.windows[0]
    assert window.worker_state == "replaced"
    assert "no longer exists" in window.reason


def test_a_kill_that_predates_the_attempt_is_not_held_against_it() -> None:
    # The attempt began *after* the kill, so it is running on the replacement.
    s1 = scheduled("n1", at=30, node_id="search")
    result = build(
        [s1, started(s1, at=30, identity="7@host-a")],
        pending_activities=[pending_started("n1", started_at=30, identity="7@host-a")],
        liveness=fleet(worker("7@host-a", heartbeat_at=58), at=60),
        chaos_events=[
            {
                "action": "kill",
                "service": "activity-worker",
                "at": (T0 + timedelta(seconds=20)).timestamp(),
            }
        ],
        now=T0 + timedelta(seconds=60),
    )
    assert result.windows[0].worker_state == "live"


def test_a_live_worker_is_reported_as_work_not_as_a_stall() -> None:
    s1 = scheduled("n1", at=0)
    result = build(
        [s1, started(s1, at=0, identity="7@host-a")],
        pending_activities=[pending_started("n1", started_at=0, identity="7@host-a")],
        liveness=fleet(worker("7@host-a", heartbeat_at=4), at=5),
        now=T0 + timedelta(seconds=5),
    )
    window = result.windows[0]
    assert window.worker_state == "live"
    assert "not a stall" in window.reason


def test_retry_backoff_is_not_confused_with_detection() -> None:
    # Both are "nothing is happening", but backoff is the retry policy waiting on
    # purpose, not the server working out whether a worker is dead.
    s1 = scheduled("n1", at=0)
    result = build(
        [s1, started(s1, at=0, identity="7@host-a")],
        pending_activities=[
            pending_scheduled(
                "n1",
                scheduled_at=0,
                attempt=2,
                next_attempt_at=90,
                retry_interval=60,
                last_failure="provider 503",
            )
        ],
        now=T0 + timedelta(seconds=60),
    )
    window = result.windows[0]
    assert window.kind == "backoff"
    assert window.clock == "retry_backoff"
    assert window.remaining_seconds == 30
    assert "provider 503" in window.reason
    assert "not a detection delay" in window.reason


def test_a_queue_with_no_worker_is_free_waiting() -> None:
    # The good case: nothing was in flight when the worker died, so there is no
    # timeout to wait out and recovery is instant once a worker returns.
    s1 = scheduled("n1", at=0, queue="ancora-cpu")
    result = build(
        [s1],
        pending_activities=[pending_scheduled("n1", scheduled_at=0)],
        # A worker exists, but not on this queue.
        liveness=fleet(worker("7@host-a", queues=["ancora-io"], heartbeat_at=28), at=30),
        now=T0 + timedelta(seconds=30),
    )
    window = result.windows[0]
    assert window.kind == "queued"
    assert window.clock is None
    assert window.queue_has_worker is False
    assert "no timeout to wait out" in window.reason


def test_the_longest_wait_is_reported_first() -> None:
    s1, s2 = scheduled("n1", at=0), scheduled("n2", at=0)
    result = build(
        [s1, s2, started(s1, at=0, identity="7@host-a"), started(s2, at=0, identity="7@host-a")],
        pending_activities=[
            pending_started("n2", started_at=55, identity="7@host-a"),
            pending_started("n1", started_at=0, identity="7@host-a"),
        ],
        now=T0 + timedelta(seconds=60),
    )
    assert [w.node_id for w in result.windows] == ["n1", "n2"]


def test_a_stuck_orchestration_step_is_its_own_window() -> None:
    task = PendingWorkflowTaskInfo(attempt=1)
    task.started_time.CopyFrom(ts(0))
    result = build(
        [wf_task_started(at=0, identity="7@host-a")],
        pending_workflow_task=task,
        workflow_task_queue="ancora-default",
        workflow_task_timeout=10.0,
        now=T0 + timedelta(seconds=4),
    )
    window = result.windows[0]
    assert window.kind == "workflow_task"
    assert window.node_id == "workflow"
    assert window.remaining_seconds == 6
    assert "short by design" in window.reason


def test_a_finished_run_is_waiting_on_nothing() -> None:
    s1 = scheduled("n1", at=0)
    result = build([s1, started(s1, at=0, identity="7@host-a"), completed(s1, at=2)])
    assert result.windows == []


# --------------------------------------------------------------------------- #
# Injections on the timeline
# --------------------------------------------------------------------------- #
def test_the_kill_appears_alongside_its_consequences() -> None:
    # The injection is the cause; the handoff is the effect. They only read as
    # cause and effect if they share one time axis, in order.
    events = [
        wf_task_started(at=0, identity="7@host-a"),
        wf_task_started(at=40, identity="7@host-b"),
    ]
    result = build(
        events,
        chaos_events=[
            {
                "action": "kill",
                "service": "worker",
                "at": (T0 + timedelta(seconds=20)).timestamp(),
                "detail": "SIGKILL → ancora-worker-1",
            }
        ],
    )
    kinds = [m.kind for m in result.markers]
    assert kinds == ["kill", "worker_changed"]
    assert result.markers[0].label == "kill worker"


def test_a_malformed_injection_record_is_skipped_not_fatal() -> None:
    result = build([], chaos_events=[{"action": "kill", "service": "worker"}])
    assert result.markers == []
