"""Queue-depth accounting for backpressure and autoscaling (AN-041, AN-047).

Backpressure needs an answer to "how much work is already in flight on this
queue?" — and it needs it on the admission hot path, so it cannot be an RPC to
Temporal per decision.

The scheduler therefore keeps its own count: admitting a node increments the
queue's in-flight gauge, and the worker reports completion when the node
finishes. That is exact for work the scheduler admitted, which is precisely the
work backpressure is meant to regulate.

The failure mode to design against is a *lost completion report* — a worker that
dies between finishing a node and reporting it. Left alone, that inflates the
gauge forever and the queue eventually refuses all work. So every admission is
recorded with a timestamp and expires after a TTL; a leaked entry costs a bounded
window of pessimism, never a permanent wedge.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

Clock = Callable[[], float]


@dataclass
class _Entry:
    queue: str
    admitted_at: float


@dataclass
class InflightTracker:
    """In-flight work per task queue, with TTL-based leak recovery."""

    ttl_seconds: float = 900.0
    clock: Clock = time.monotonic
    _entries: dict[str, _Entry] = field(default_factory=dict)
    # Cumulative counters for the metrics endpoint.
    _admitted_total: dict[str, int] = field(default_factory=dict)
    _completed_total: dict[str, int] = field(default_factory=dict)
    _expired_total: dict[str, int] = field(default_factory=dict)

    def _sweep(self) -> None:
        now = self.clock()
        expired = [k for k, e in self._entries.items() if now - e.admitted_at > self.ttl_seconds]
        for k in expired:
            queue = self._entries.pop(k).queue
            self._expired_total[queue] = self._expired_total.get(queue, 0) + 1

    def admit(self, token: str, queue: str) -> None:
        """Record an admitted unit of work. ``token`` is ``run_id:node_id:attempt``."""
        self._sweep()
        self._entries[token] = _Entry(queue=queue, admitted_at=self.clock())
        self._admitted_total[queue] = self._admitted_total.get(queue, 0) + 1

    def complete(self, token: str) -> bool:
        """Mark work finished. Returns False for an unknown/expired token."""
        entry = self._entries.pop(token, None)
        if entry is None:
            return False
        self._completed_total[entry.queue] = self._completed_total.get(entry.queue, 0) + 1
        return True

    def depth(self, queue: str) -> int:
        self._sweep()
        return sum(1 for e in self._entries.values() if e.queue == queue)

    def depths(self) -> dict[str, int]:
        self._sweep()
        out: dict[str, int] = {}
        for e in self._entries.values():
            out[e.queue] = out.get(e.queue, 0) + 1
        # Report zero for queues we have seen, so a drained queue shows 0 rather
        # than vanishing from the metrics and breaking rate() in Prometheus.
        for q in self._admitted_total:
            out.setdefault(q, 0)
        return out

    def counters(self) -> dict[str, dict[str, int]]:
        return {
            "admitted": dict(self._admitted_total),
            "completed": dict(self._completed_total),
            "expired": dict(self._expired_total),
        }

    def reset(self) -> None:
        self._entries.clear()
        self._admitted_total.clear()
        self._completed_total.clear()
        self._expired_total.clear()


@dataclass(frozen=True)
class BackpressureDecision:
    admitted: bool
    retry_after: float = 0.0
    depth: int = 0
    soft: int = 0
    hard: int = 0


def evaluate_backpressure(
    *,
    depth: int,
    soft: int,
    hard: int,
    priority: int,
    priority_cutoff: int,
    backoff_seconds: float,
    max_backoff_seconds: float,
) -> BackpressureDecision:
    """Pure watermark rule — shed load by lane as a queue fills.

    Below ``soft``: admit everything. Between ``soft`` and ``hard``: admit only
    lanes at or above ``priority_cutoff`` in urgency (numerically ``<=``), so
    interactive work keeps flowing while bulk backfill waits. At ``hard``: defer
    everything, including high priority — past this point admitting more only
    deepens the queue nobody is draining.

    The backoff grows linearly with overshoot so a badly overloaded queue tells
    callers to wait proportionally longer instead of thrashing.
    """
    if depth < soft:
        return BackpressureDecision(admitted=True, depth=depth, soft=soft, hard=hard)

    span = max(hard - soft, 1)
    overshoot = (depth - soft) / span
    retry_after = min(backoff_seconds * (1.0 + overshoot), max_backoff_seconds)

    if depth >= hard:
        return BackpressureDecision(
            admitted=False, retry_after=retry_after, depth=depth, soft=soft, hard=hard
        )
    if priority <= priority_cutoff:
        return BackpressureDecision(admitted=True, depth=depth, soft=soft, hard=hard)
    return BackpressureDecision(
        admitted=False, retry_after=retry_after, depth=depth, soft=soft, hard=hard
    )
