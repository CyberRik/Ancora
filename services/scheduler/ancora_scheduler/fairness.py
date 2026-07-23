"""Weighted fair queuing across tenants on a shared queue (AN-042).

Without this, one org submitting ten thousand nodes starves everyone else on the
same worker pool: Temporal is FIFO per queue, so whoever enqueues first wins.
The fix is the classic *virtual time* scheme from packet scheduling (WFQ/SFQ).

Each tenant carries a virtual clock. Admitting one unit of work advances that
tenant's clock by ``cost / weight`` — so a weight-3 tenant's clock advances a
third as fast per admission and it therefore gets three admissions for every one
of a weight-1 tenant. A tenant is deferred only while its clock is ahead of the
slowest *active* tenant's by more than one unit of its own work. If nobody else
is competing, nobody is ever deferred — fairness costs nothing on an idle queue.

Two details keep it honest:

* **Newcomers adopt the current minimum** rather than starting at zero. Starting
  at zero would let a tenant that has been idle for an hour monopolize the queue
  until its banked credit ran out.
* **Idle tenants are pruned** from the active set, so the minimum tracks who is
  actually competing right now, not who competed this morning.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

Clock = Callable[[], float]


@dataclass
class _TenantState:
    virtual_time: float = 0.0
    last_seen: float = 0.0


@dataclass(frozen=True)
class FairDecision:
    admitted: bool
    retry_after: float = 0.0
    # Diagnostics for GET /v1/scheduler/fairness and the defer reason string.
    virtual_time: float = 0.0
    min_virtual_time: float = 0.0
    contenders: int = 1


class FairShare:
    """Per-queue weighted fair queuing over tenants."""

    def __init__(
        self, *, idle_seconds: float = 30.0, defer_seconds: float = 0.25, clock: Clock | None = None
    ) -> None:
        self._idle_seconds = idle_seconds
        self._defer_seconds = defer_seconds
        self._clock = clock or time.monotonic
        self._queues: dict[str, dict[str, _TenantState]] = {}

    # ---- internals ------------------------------------------------------ #
    def _active(self, queue: str, now: float) -> dict[str, _TenantState]:
        tenants = self._queues.setdefault(queue, {})
        stale = [t for t, s in tenants.items() if now - s.last_seen > self._idle_seconds]
        for t in stale:
            del tenants[t]
        return tenants

    # ---- public API ----------------------------------------------------- #
    def admit(
        self, queue: str, tenant: str, *, weight: float = 1.0, cost: float = 1.0
    ) -> FairDecision:
        """Decide whether ``tenant`` may take a slot on ``queue`` right now."""
        if weight <= 0:
            raise ValueError("fair-share weight must be positive")
        now = self._clock()
        tenants = self._active(queue, now)

        min_vt = min((s.virtual_time for s in tenants.values()), default=0.0)
        state = tenants.get(tenant)
        if state is None:
            # A newcomer joins at the current front of the line, not at zero.
            state = _TenantState(virtual_time=min_vt, last_seen=now)
            tenants[tenant] = state
        state.last_seen = now

        step = cost / weight
        contended = len(tenants) > 1
        if contended and state.virtual_time > min_vt + step:
            return FairDecision(
                admitted=False,
                retry_after=self._defer_seconds,
                virtual_time=state.virtual_time,
                min_virtual_time=min_vt,
                contenders=len(tenants),
            )

        state.virtual_time += step
        return FairDecision(
            admitted=True,
            virtual_time=state.virtual_time,
            min_virtual_time=min_vt,
            contenders=len(tenants),
        )

    def refund(self, queue: str, tenant: str, *, weight: float = 1.0, cost: float = 1.0) -> None:
        """Undo an admission's virtual-time charge.

        Admission is a pipeline: fair share is charged before the rate limiter
        runs, because charging after would let a rate-limited tenant hold its
        place forever. When a later stage defers, the charge must come back or the
        tenant is billed for work it never got to do.
        """
        state = self._queues.get(queue, {}).get(tenant)
        if state is not None:
            state.virtual_time = max(0.0, state.virtual_time - cost / weight)

    def snapshot(self, queue: str) -> dict[str, float]:
        """Current virtual times for the active tenants on ``queue`` (diagnostics)."""
        now = self._clock()
        return {t: s.virtual_time for t, s in self._active(queue, now).items()}

    def reset(self) -> None:
        self._queues.clear()


@dataclass
class PriorityLanes:
    """Lane bookkeeping for priority-aware backpressure (AN-043).

    Temporal itself orders the queue via ``Priority.priority_key``; this is the
    scheduler's own view, used to decide *which* lanes to shed when a queue is
    over its soft watermark and to expose per-lane counters as metrics.
    """

    counts: dict[tuple[str, int], int] = field(default_factory=dict)

    def record(self, queue: str, priority: int) -> None:
        key = (queue, priority)
        self.counts[key] = self.counts.get(key, 0) + 1

    def by_queue(self, queue: str) -> dict[int, int]:
        return {p: n for (q, p), n in self.counts.items() if q == queue}
