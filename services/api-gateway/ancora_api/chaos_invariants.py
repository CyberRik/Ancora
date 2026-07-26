"""Chaos invariants: the properties a fault must NOT break (Phase 5, RFC-0010).

A chaos experiment is only worth anything if it *asserts*. Killing a worker and
watching the run finish is a nice demo; proving that it finished *without losing
state or double-firing an effect* is the actual guarantee. These are the checks
that turn a demo into a regression test.

Every checker is a pure function over Temporal history (the source of truth) plus,
for the effect invariant, the inbox rows — so the assertions are pinned against
synthetic post-kill histories in CI, exactly like the recovery and graph views.
The three invariants:

* **no re-executed activities** — Temporal's exactly-once contract: an activity
  that recorded a result is a durable checkpoint and must never run again, even
  after a worker dies mid-run. A second completion for one scheduled activity is
  lost-state, drawn as double work.
* **exactly-once effects** — every side-effecting node's idempotency guard
  committed; none is stranded ``pending`` (a half-done effect a retry could
  repeat).
* **run completed** — the durable state carried the run through the fault to a
  terminal *completed* state; the kill lost nothing.

Plus :func:`measure_rto` — the recovery time: kill → the fleet picking work back up.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent

from ancora_api.history import dt


@dataclass(frozen=True)
class Invariant:
    """The verdict of one invariant check."""

    name: str
    description: str
    passed: bool
    detail: str


def _completions_by_scheduled(events: Sequence[HistoryEvent]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for e in events:
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            sid = e.activity_task_completed_event_attributes.scheduled_event_id
            counts[sid] = counts.get(sid, 0) + 1
    return counts


def no_reexecuted_activities(events: Sequence[HistoryEvent]) -> Invariant:
    """No scheduled activity recorded more than one completion (exactly-once)."""
    counts = _completions_by_scheduled(events)
    doubled = [sid for sid, n in counts.items() if n > 1]
    passed = not doubled
    detail = (
        f"{len(counts)} activities completed, each exactly once"
        if passed
        else f"{len(doubled)} activity(ies) completed more than once (re-executed): {doubled}"
    )
    return Invariant(
        name="no_reexecuted_activities",
        description="A completed activity is a durable checkpoint; a kill must not re-run it.",
        passed=passed,
        detail=detail,
    )


def run_completed(events: Sequence[HistoryEvent]) -> Invariant:
    """The workflow reached a terminal *completed* state despite the fault."""
    completed = any(
        e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED for e in events
    )
    failed = any(
        e.event_type
        in (
            EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED,
            EventType.EVENT_TYPE_WORKFLOW_EXECUTION_TERMINATED,
            EventType.EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT,
        )
        for e in events
    )
    if completed:
        detail = "workflow reached Completed — no state lost to the fault"
    elif failed:
        detail = "workflow did not complete: it failed/terminated after the fault"
    else:
        detail = "workflow has not reached a terminal state yet"
    return Invariant(
        name="no_lost_state",
        description="The run survived the fault and finished; durable state carried it through.",
        passed=completed,
        detail=detail,
    )


@dataclass(frozen=True)
class InboxRecord:
    """The slice of an inbox row the effect invariant needs."""

    key: str
    status: str  # "pending" | "done"


def exactly_once_effects(inbox: Sequence[InboxRecord]) -> Invariant:
    """Every side-effecting node's guard committed exactly once; none left pending.

    The inbox's unique key structurally forbids two rows per effect, so the risk a
    kill introduces is the *other* direction: an effect that began (row written
    ``pending``) but whose worker died before committing the result. A retry that
    finds a stale ``pending`` row could re-fire. A clean run leaves every key
    ``done``.
    """
    pending = [r.key for r in inbox if r.status != "done"]
    passed = not pending
    detail = (
        f"{len(inbox)} guarded effect(s), all committed exactly once"
        if passed
        else f"{len(pending)} effect(s) left uncommitted (pending): {pending}"
    )
    return Invariant(
        name="exactly_once_effects",
        description="Side-effecting nodes fire once; a kill mid-effect must not double it.",
        passed=passed,
        detail=detail,
    )


def measure_rto(events: Sequence[HistoryEvent], kill_at: datetime) -> float | None:
    """Recovery time: kill → the fleet resuming work, in seconds.

    Recovery is the first activity to *start* after the kill — a surviving worker
    picking up the redelivered task — or, failing any post-kill activity, the
    workflow's completion. ``None`` when history shows no recovery after the kill
    (nothing resumed, or the kill timestamp is after everything recorded).
    """
    candidates: list[datetime] = []
    for e in events:
        when = dt(e.event_time)
        if when is None or when <= kill_at:
            continue
        if e.event_type in (
            EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED,
            EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED,
        ):
            candidates.append(when)
    if not candidates:
        return None
    return round((min(candidates) - kill_at).total_seconds(), 3)


def evaluate(events: Sequence[HistoryEvent], inbox: Sequence[InboxRecord]) -> list[Invariant]:
    """Run every history/inbox invariant and return their verdicts."""
    return [
        run_completed(events),
        no_reexecuted_activities(events),
        exactly_once_effects(inbox),
    ]
