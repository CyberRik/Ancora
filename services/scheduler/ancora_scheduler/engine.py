"""The admission engine (AN-038) — one decision, five governors.

Every node dispatch asks the same question before it runs: *may this start now?*
The engine answers ``admit``, ``defer(retry_after)``, or ``reject(reason)``.

``defer`` is the interesting outcome and the reason this design works at all.
Because the caller is a Temporal activity, a deferral is not a dropped request:
the activity raises a retryable error carrying ``retry_after``, Temporal parks
the work in durable history and re-delivers it later. Backpressure therefore
costs nothing but time — no queue in the scheduler, no state to lose, no
thundering herd on recovery. The scheduler can restart mid-overload and the
deferred work is still safely in Temporal.

**Order of governors matters**, because two of them consume a resource:

1. ``deadline`` — reject. Work that cannot finish in time should never start.
2. ``budget`` — reject (hard mode) or warn. Cheap, and consumes nothing.
3. ``backpressure`` — defer. Consumes nothing; reading a gauge.
4. ``fairness`` — defer. *Charges* the tenant's virtual clock.
5. ``rate limit`` — defer. *Takes* provider tokens.

Fairness is charged before the rate limiter so that a rate-limited tenant keeps
its place in line rather than re-competing from scratch each attempt. If the rate
limiter then defers, the fairness charge is refunded — otherwise a tenant blocked
by a provider quota would be billed for throughput it never received.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from ancora_common.ratelimit import InMemoryRateLimiter, RateLimitConfig
from ancora_scheduler.backlog import InflightTracker, evaluate_backpressure
from ancora_scheduler.budget import BudgetLedger, check_budget
from ancora_scheduler.config import ConfigStore
from ancora_scheduler.fairness import FairShare, PriorityLanes

logger = logging.getLogger("ancora.scheduler.engine")

Outcome = Literal["admit", "defer", "reject"]


@dataclass(frozen=True)
class AdmissionRequest:
    """One node's request to start. Mirrors the HTTP schema in :mod:`api`."""

    run_id: str
    node_id: str
    node_type: str
    task_queue: str
    tenant: str = "default"
    priority: int = 3
    attempt: int = 1
    provider: str | None = None
    model: str | None = None
    # Bucket tokens this call consumes — a batch of 10 prompts costs 10.
    tokens: float = 1.0
    estimated_usd: float = 0.0
    # Seconds left on the run's deadline; None = no deadline.
    deadline_seconds: float | None = None

    @property
    def token(self) -> str:
        """Stable id for the in-flight gauge; retries reuse the same slot."""
        return f"{self.run_id}:{self.node_id}"


@dataclass(frozen=True)
class AdmissionDecision:
    outcome: Outcome
    # Which governor decided. "none" when admitted without contention.
    rule: str = "none"
    retry_after: float = 0.0
    reason: str = ""
    # Non-blocking budget signal, surfaced to the caller and the UI.
    warning: str | None = None
    # Deadline-derived ceiling for this attempt (AN-046), when a deadline is set.
    timeout_seconds: float | None = None
    queue_depth: int = 0

    @property
    def admitted(self) -> bool:
        return self.outcome == "admit"


@dataclass
class AdmissionStats:
    """Cumulative counters exported as Prometheus metrics (AN-047)."""

    by_outcome: dict[str, int] = field(default_factory=dict)
    by_rule: dict[str, int] = field(default_factory=dict)
    by_queue_outcome: dict[tuple[str, str], int] = field(default_factory=dict)
    rate_limit_defers: dict[str, int] = field(default_factory=dict)

    def record(self, queue: str, decision: AdmissionDecision, provider: str | None) -> None:
        self.by_outcome[decision.outcome] = self.by_outcome.get(decision.outcome, 0) + 1
        self.by_rule[decision.rule] = self.by_rule.get(decision.rule, 0) + 1
        key = (queue, decision.outcome)
        self.by_queue_outcome[key] = self.by_queue_outcome.get(key, 0) + 1
        if decision.rule == "rate_limit" and provider:
            self.rate_limit_defers[provider] = self.rate_limit_defers.get(provider, 0) + 1

    def reset(self) -> None:
        self.by_outcome.clear()
        self.by_rule.clear()
        self.by_queue_outcome.clear()
        self.rate_limit_defers.clear()


class AdmissionEngine:
    """Stateful admission control. Single-process; state is reconstructible."""

    def __init__(
        self,
        store: ConfigStore | None = None,
        *,
        limiter: InMemoryRateLimiter | None = None,
        inflight: InflightTracker | None = None,
        fair: FairShare | None = None,
        ledger: BudgetLedger | None = None,
    ) -> None:
        self.store = store or ConfigStore()
        cfg = self.store.config
        self.limiter = limiter or InMemoryRateLimiter()
        self.inflight = inflight or InflightTracker(ttl_seconds=cfg.inflight_ttl_seconds)
        self.fair = fair or FairShare(
            idle_seconds=cfg.fairness.idle_seconds, defer_seconds=cfg.fairness.defer_seconds
        )
        self.ledger = ledger or BudgetLedger()
        self.lanes = PriorityLanes()
        self.stats = AdmissionStats()

    # ---- the decision --------------------------------------------------- #
    def admit(self, req: AdmissionRequest) -> AdmissionDecision:
        self.store.reload_if_changed()
        cfg = self.store.config
        tenant_policy = cfg.tenant(req.tenant)
        depth = self.inflight.depth(req.task_queue)

        def finish(decision: AdmissionDecision) -> AdmissionDecision:
            self.stats.record(req.task_queue, decision, req.provider)
            return decision

        # 1. Deadline (AN-046) — reject work that is already out of time.
        timeout_seconds: float | None = None
        if req.deadline_seconds is not None:
            if req.deadline_seconds <= 0:
                return finish(
                    AdmissionDecision(
                        outcome="reject",
                        rule="deadline",
                        reason=f"run deadline passed before {req.node_id} could start",
                        queue_depth=depth,
                    )
                )
            timeout_seconds = req.deadline_seconds

        # 2. Budget (AN-045) — run scope first, then the tenant's pooled budget.
        warning: str | None = None
        run_limit = tenant_policy.budget_usd or cfg.budget.default_run_usd
        run_verdict = check_budget(
            spent_usd=self.ledger.run_spend(req.run_id),
            estimated_usd=req.estimated_usd,
            limit_usd=run_limit,
            mode=cfg.budget.mode,
            warn_at=cfg.budget.warn_at,
            scope=f"run {req.run_id}",
        )
        if not run_verdict.allowed:
            return finish(
                AdmissionDecision(
                    outcome="reject",
                    rule="budget",
                    reason=run_verdict.reason or "budget exceeded",
                    queue_depth=depth,
                )
            )
        warning = run_verdict.warning

        # 3. Backpressure (AN-041) — shed by lane as the queue fills.
        mark = cfg.watermark_for(req.task_queue)
        bp = evaluate_backpressure(
            depth=depth,
            soft=mark.soft,
            hard=mark.hard,
            priority=req.priority,
            priority_cutoff=cfg.backpressure_priority_cutoff,
            backoff_seconds=mark.backoff_seconds,
            max_backoff_seconds=mark.max_backoff_seconds,
        )
        if not bp.admitted:
            return finish(
                AdmissionDecision(
                    outcome="defer",
                    rule="backpressure",
                    retry_after=bp.retry_after,
                    reason=(
                        f"{req.task_queue} depth {bp.depth} is at or above its "
                        f"{'hard' if bp.depth >= bp.hard else 'soft'} watermark "
                        f"({bp.hard if bp.depth >= bp.hard else bp.soft})"
                    ),
                    warning=warning,
                    timeout_seconds=timeout_seconds,
                    queue_depth=depth,
                )
            )

        # 4. Fair share (AN-042) — charged now, refunded below if step 5 defers.
        weight = tenant_policy.weight
        charged = False
        if cfg.fairness.enabled:
            fd = self.fair.admit(req.task_queue, req.tenant, weight=weight, cost=req.tokens)
            if not fd.admitted:
                return finish(
                    AdmissionDecision(
                        outcome="defer",
                        rule="fairness",
                        retry_after=fd.retry_after,
                        reason=(
                            f"tenant {req.tenant!r} is ahead of its share on "
                            f"{req.task_queue} ({fd.contenders} tenants competing)"
                        ),
                        warning=warning,
                        timeout_seconds=timeout_seconds,
                        queue_depth=depth,
                    )
                )
            charged = True

        # 5. Provider rate limit (AN-040) — the last gate, because it takes tokens.
        rule = cfg.rate_limit_for(req.provider, req.model)
        if rule is not None and req.provider:
            key = f"{req.provider}/{req.model}" if req.model else req.provider
            rl = self.limiter.admit(
                key, RateLimitConfig(rps=rule.rps, burst=rule.burst), want=req.tokens
            )
            if not rl.admitted:
                if charged:
                    self.fair.refund(req.task_queue, req.tenant, weight=weight, cost=req.tokens)
                return finish(
                    AdmissionDecision(
                        outcome="defer",
                        rule="rate_limit",
                        retry_after=rl.retry_after,
                        reason=(
                            f"{key} rate limit reached ({rule.rps}/s, burst {rule.burst}); "
                            f"deferring {req.node_id} for {rl.retry_after:.2f}s"
                        ),
                        warning=warning,
                        timeout_seconds=timeout_seconds,
                        queue_depth=depth,
                    )
                )

        # Admitted: take an in-flight slot and record the lane.
        self.inflight.admit(req.token, req.task_queue)
        self.lanes.record(req.task_queue, req.priority)
        return finish(
            AdmissionDecision(
                outcome="admit",
                warning=warning,
                timeout_seconds=timeout_seconds,
                queue_depth=depth + 1,
            )
        )

    # ---- feedback from workers ------------------------------------------ #
    def complete(
        self, *, run_id: str, node_id: str, tenant: str = "default", usd: float = 0.0
    ) -> bool:
        """Release the in-flight slot and record spend when a node finishes."""
        self.ledger.record(run_id=run_id, tenant=tenant, usd=usd)
        return self.inflight.complete(f"{run_id}:{node_id}")

    def reset(self) -> None:
        """Drop all runtime state (tests; not used in production)."""
        self.inflight.reset()
        self.fair.reset()
        self.ledger.reset()
        self.stats.reset()
        self.lanes.counts.clear()
