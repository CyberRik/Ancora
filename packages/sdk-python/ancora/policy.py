"""Deterministic policy resolution for node dispatch (AN-039, AN-044, AN-046).

A workflow must decide *where* and *how* every node runs — task queue, priority,
timeouts, retry policy — and it must decide identically on every replay. That
rules out reading config, clocks, or the environment from workflow code. So the
policy table lives here as **pure data plus a pure function**: given a node type
and an optional per-call override from the ``dag_spec``, :func:`resolve_policy`
returns the same :class:`NodePolicy` forever, on the original run and on every
replay years later.

Three concerns are resolved together because they interact:

* **Routing** — node class → capability → task queue (AN-033), plus a priority
  lane (AN-043) so urgent work does not queue behind a bulk backlog.
* **Retry** (AN-044) — different node classes fail differently. An LLM call is
  worth retrying patiently through rate limits; a SQL query that failed on bad
  input will fail identically forever. Each class therefore gets its own backoff
  shape and its own non-retryable error set. Jitter is applied by the Temporal
  *server*, not here — computing jitter in workflow code would be non-deterministic.
* **Deadlines** (AN-046) — a run-level deadline is turned into a
  ``schedule_to_close_timeout`` so a node inherits the deadline rather than
  outliving it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Final

from temporalio.common import RetryPolicy

from ancora_common.resources import Capability, queue_for

# Priority lanes (AN-043). Lower number = drains first. Temporal's own priority
# key uses the same convention, and the scheduler uses it for lane accounting.
PRIORITY_HIGH: Final = 1
PRIORITY_NORMAL: Final = 3
PRIORITY_BULK: Final = 5

_PRIORITY_NAMES: Final[dict[str, int]] = {
    "high": PRIORITY_HIGH,
    "normal": PRIORITY_NORMAL,
    "bulk": PRIORITY_BULK,
}


@dataclass(frozen=True)
class RetrySpec:
    """A retry shape, expressed in plain numbers so it is trivially comparable.

    Kept separate from Temporal's :class:`RetryPolicy` so the table above is pure
    data (diffable, testable, serializable into ``dag_spec``) and the Temporal
    object is built only at the dispatch boundary.
    """

    initial_seconds: float = 1.0
    backoff_coefficient: float = 2.0
    maximum_seconds: float = 60.0
    maximum_attempts: int = 3
    # Error *types* that must never be retried, matched against
    # ``ApplicationError.type``. Terminal node failures use "NodeError" plus
    # ``non_retryable``; these names cover errors raised outside a node.
    non_retryable: tuple[str, ...] = ()

    def to_temporal(self) -> RetryPolicy:
        return RetryPolicy(
            initial_interval=timedelta(seconds=self.initial_seconds),
            backoff_coefficient=self.backoff_coefficient,
            maximum_interval=timedelta(seconds=self.maximum_seconds),
            maximum_attempts=self.maximum_attempts,
            non_retryable_error_types=list(self.non_retryable) or None,
        )


@dataclass(frozen=True)
class NodePolicy:
    """The fully-resolved dispatch options for one node call."""

    task_queue: str
    priority: int
    start_to_close: timedelta
    retry: RetrySpec
    schedule_to_close: timedelta | None = None
    heartbeat: timedelta | None = None


@dataclass(frozen=True)
class _ClassDefaults:
    capability: Capability
    start_to_close_seconds: float
    retry: RetrySpec
    heartbeat_seconds: float | None = None


# --------------------------------------------------------------------------- #
# The policy table (AN-044). One entry per built-in node class.
# --------------------------------------------------------------------------- #
_DEFAULTS: Final[dict[str, _ClassDefaults]] = {
    # LLM calls are slow, expensive, and fail transiently (429s, provider blips).
    # Retry patiently and for a long time — losing a half-finished chain costs
    # more than waiting. Capped at 5 minutes per attempt.
    "llm": _ClassDefaults(
        capability=Capability.CPU,
        start_to_close_seconds=300.0,
        retry=RetrySpec(
            initial_seconds=2.0,
            backoff_coefficient=2.0,
            maximum_seconds=120.0,
            maximum_attempts=6,
        ),
    ),
    # HTTP: fast, so retry quickly, but give up sooner — a genuinely broken
    # endpoint should surface to the workflow rather than be hammered.
    "http": _ClassDefaults(
        capability=Capability.IO,
        start_to_close_seconds=60.0,
        retry=RetrySpec(
            initial_seconds=1.0,
            backoff_coefficient=2.0,
            maximum_seconds=30.0,
            maximum_attempts=4,
        ),
    ),
    # SQL failures are usually deterministic (bad query, constraint violation).
    # Retry a couple of times to ride out a connection blip, then stop.
    "database": _ClassDefaults(
        capability=Capability.IO,
        start_to_close_seconds=120.0,
        retry=RetrySpec(
            initial_seconds=1.0,
            backoff_coefficient=2.0,
            maximum_seconds=15.0,
            maximum_attempts=3,
        ),
    ),
    # User code. Assume a crash is a real bug, not a blip: one retry, then fail
    # loudly. Heartbeats let a long-running function be cancelled promptly.
    "python": _ClassDefaults(
        capability=Capability.CPU,
        start_to_close_seconds=600.0,
        heartbeat_seconds=30.0,
        retry=RetrySpec(
            initial_seconds=1.0,
            backoff_coefficient=2.0,
            maximum_seconds=10.0,
            maximum_attempts=2,
        ),
    ),
    # An approval gate never runs as an activity (see ancora.nodes.approval); the
    # entry exists so resolution never falls through to the generic default for a
    # known type, and so a misdispatch fails fast instead of retrying.
    "approval": _ClassDefaults(
        capability=Capability.IO,
        start_to_close_seconds=10.0,
        retry=RetrySpec(maximum_attempts=1),
    ),
}

_GENERIC: Final = _ClassDefaults(
    capability=Capability.CPU,
    start_to_close_seconds=60.0,
    retry=RetrySpec(),
)


def _as_priority(value: Any) -> int | None:
    """Accept ``"high"``/``"normal"``/``"bulk"`` or a raw integer lane."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    name = str(value).strip().lower()
    if name not in _PRIORITY_NAMES:
        raise ValueError(f"unknown priority {value!r}; expected one of {sorted(_PRIORITY_NAMES)}")
    return _PRIORITY_NAMES[name]


def _seconds(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def resolve_policy(
    type_name: str,
    overrides: dict[str, Any] | None = None,
    *,
    deadline_remaining: timedelta | None = None,
) -> NodePolicy:
    """Resolve dispatch options for ``type_name`` — pure, hence replay-safe.

    ``overrides`` is the per-node stanza from the workflow's ``dag_spec`` (or a
    literal dict at the call site) and may set ``capability``, ``task_queue``,
    ``priority``, ``timeout_seconds``, and any :class:`RetrySpec` field. Unknown
    node types resolve to a conservative generic policy rather than raising, so a
    third-party node (Phase 5) is dispatchable before it has a table entry.

    ``deadline_remaining`` is the time left on the run's deadline (AN-046). When
    given it becomes the ``schedule_to_close_timeout``, so a node cannot outlive
    the deadline it was admitted under; the per-attempt timeout is clamped to it
    as well. The caller computes it from workflow-deterministic values (start
    time from ``workflow.info()``), keeping this function pure.
    """
    over = overrides or {}
    base = _DEFAULTS.get(type_name, _GENERIC)

    capability = Capability(over.get("capability", base.capability))
    task_queue = str(over.get("task_queue") or queue_for(capability))
    priority = _as_priority(over.get("priority")) or PRIORITY_NORMAL

    retry = base.retry
    retry_over = {
        field: over[key]
        for field, key in (
            ("initial_seconds", "retry_initial_seconds"),
            ("backoff_coefficient", "retry_backoff_coefficient"),
            ("maximum_seconds", "retry_maximum_seconds"),
            ("maximum_attempts", "max_attempts"),
        )
        if over.get(key) is not None
    }
    if retry_over:
        retry = replace(retry, **retry_over)

    start_to_close_seconds = _seconds(over.get("timeout_seconds")) or base.start_to_close_seconds
    heartbeat_seconds = _seconds(over.get("heartbeat_seconds")) or base.heartbeat_seconds

    schedule_to_close: timedelta | None = None
    if deadline_remaining is not None:
        # Never schedule work that cannot finish before the deadline, and never
        # let one attempt run past it either.
        remaining = max(deadline_remaining, timedelta(seconds=0))
        schedule_to_close = remaining
        start_to_close_seconds = min(start_to_close_seconds, max(remaining.total_seconds(), 1.0))

    return NodePolicy(
        task_queue=task_queue,
        priority=priority,
        start_to_close=timedelta(seconds=start_to_close_seconds),
        schedule_to_close=schedule_to_close,
        heartbeat=timedelta(seconds=heartbeat_seconds) if heartbeat_seconds else None,
        retry=retry,
    )


def retry_policy_for(type_name: str) -> RetryPolicy:
    """The Temporal retry policy a node class gets by default (AN-044)."""
    return _DEFAULTS.get(type_name, _GENERIC).retry.to_temporal()
