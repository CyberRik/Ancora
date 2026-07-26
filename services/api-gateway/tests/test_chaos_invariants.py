"""The chaos invariants — the properties a fault must not break.

A chaos experiment's whole value is the assertion, so the assertion logic is
pinned here against synthetic post-kill histories: a re-executed activity, a
half-committed effect, a run that failed instead of recovering. Each is a way the
durability claim could be quietly false, and each must show up as a failed
invariant rather than a green demo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from google.protobuf.timestamp_pb2 import Timestamp
from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent

from ancora_api.chaos_invariants import (
    InboxRecord,
    evaluate,
    exactly_once_effects,
    measure_rto,
    no_reexecuted_activities,
    run_completed,
)

T0 = datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)


def _ts(offset: float) -> Timestamp:
    stamp = Timestamp()
    stamp.FromDatetime(T0 + timedelta(seconds=offset))
    return stamp


def completed(scheduled_event_id: int, *, at: float) -> HistoryEvent:
    e = HistoryEvent(event_id=scheduled_event_id + 1000, event_time=_ts(at))
    e.event_type = EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
    e.activity_task_completed_event_attributes.scheduled_event_id = scheduled_event_id
    return e


def started(*, at: float) -> HistoryEvent:
    e = HistoryEvent(event_id=1, event_time=_ts(at))
    e.event_type = EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED
    return e


def wf_completed(*, at: float) -> HistoryEvent:
    e = HistoryEvent(event_id=9, event_time=_ts(at))
    e.event_type = EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED
    return e


def wf_failed(*, at: float) -> HistoryEvent:
    e = HistoryEvent(event_id=9, event_time=_ts(at))
    e.event_type = EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED
    return e


# --------------------------------------------------------------------------- #
# no re-executed activities
# --------------------------------------------------------------------------- #
def test_distinct_activities_completing_once_each_passes() -> None:
    events = [completed(2, at=5), completed(3, at=6), completed(4, at=7)]
    result = no_reexecuted_activities(events)
    assert result.passed
    assert "each exactly once" in result.detail


def test_one_scheduled_activity_completing_twice_is_lost_state() -> None:
    # The same durable checkpoint recorded twice: the kill made the workflow re-run
    # work it had already finished. This must fail.
    events = [completed(2, at=5), completed(2, at=40)]
    result = no_reexecuted_activities(events)
    assert not result.passed
    assert "more than once" in result.detail


# --------------------------------------------------------------------------- #
# no lost state (run completed)
# --------------------------------------------------------------------------- #
def test_run_that_reached_completed_passes() -> None:
    assert run_completed([completed(2, at=5), wf_completed(at=10)]).passed


def test_run_that_failed_after_the_fault_does_not_pass() -> None:
    result = run_completed([completed(2, at=5), wf_failed(at=10)])
    assert not result.passed
    assert "failed/terminated" in result.detail


def test_run_still_in_flight_does_not_pass_yet() -> None:
    result = run_completed([completed(2, at=5)])
    assert not result.passed
    assert "not reached a terminal" in result.detail


# --------------------------------------------------------------------------- #
# exactly-once effects
# --------------------------------------------------------------------------- #
def test_all_effects_committed_passes() -> None:
    inbox = [InboxRecord("k1", "done"), InboxRecord("k2", "done")]
    assert exactly_once_effects(inbox).passed


def test_no_effects_at_all_passes_vacuously() -> None:
    assert exactly_once_effects([]).passed


def test_an_effect_stuck_pending_fails() -> None:
    # A worker died mid-effect: the guard wrote the row but never committed. A
    # retry could re-fire it, so exactly-once is not proven.
    inbox = [InboxRecord("k1", "done"), InboxRecord("k2", "pending")]
    result = exactly_once_effects(inbox)
    assert not result.passed
    assert "k2" in result.detail


# --------------------------------------------------------------------------- #
# RTO
# --------------------------------------------------------------------------- #
def test_rto_is_time_from_kill_to_first_recovery() -> None:
    kill_at = T0 + timedelta(seconds=10)
    # A survivor starts the redelivered activity 8s after the kill.
    events = [started(at=2), started(at=18), wf_completed(at=25)]
    assert measure_rto(events, kill_at) == 8.0


def test_rto_is_none_when_nothing_resumes_after_the_kill() -> None:
    kill_at = T0 + timedelta(seconds=30)
    events = [started(at=2), completed(2, at=5)]  # all before the kill
    assert measure_rto(events, kill_at) is None


# --------------------------------------------------------------------------- #
# evaluate
# --------------------------------------------------------------------------- #
def test_evaluate_runs_every_invariant() -> None:
    events = [completed(2, at=5), wf_completed(at=10)]
    names = {r.name for r in evaluate(events, [InboxRecord("k", "done")])}
    assert names == {"no_lost_state", "no_reexecuted_activities", "exactly_once_effects"}
