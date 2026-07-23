"""Admission engine tests (AN-049).

Covers every governor's decision path and — more importantly — the *interactions*
between them: which one wins, and what happens to a resource one governor has
already consumed when a later one defers.

All timing is driven by an injected clock, so nothing here sleeps and nothing is
flaky.
"""

from __future__ import annotations

import pytest

from ancora_common.ratelimit import InMemoryRateLimiter
from ancora_scheduler.backlog import InflightTracker, evaluate_backpressure
from ancora_scheduler.budget import BudgetLedger, check_budget
from ancora_scheduler.config import (
    BudgetConfig,
    ConfigStore,
    FairnessConfig,
    RateLimitRule,
    SchedulerConfig,
    TenantPolicy,
    Watermark,
)
from ancora_scheduler.engine import AdmissionEngine, AdmissionRequest
from ancora_scheduler.fairness import FairShare


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_engine(config: SchedulerConfig, clock: FakeClock | None = None) -> AdmissionEngine:
    clock = clock or FakeClock()
    return AdmissionEngine(
        ConfigStore(path=None, config=config),
        limiter=InMemoryRateLimiter(clock=clock),
        inflight=InflightTracker(ttl_seconds=900.0, clock=clock),
        fair=FairShare(
            idle_seconds=config.fairness.idle_seconds,
            defer_seconds=config.fairness.defer_seconds,
            clock=clock,
        ),
        ledger=BudgetLedger(),
    )


def req(**over: object) -> AdmissionRequest:
    base = {
        "run_id": "wf-1",
        "node_id": "n1",
        "node_type": "llm",
        "task_queue": "ancora-cpu",
    }
    base.update(over)
    return AdmissionRequest(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Baseline
# --------------------------------------------------------------------------- #
def test_admits_when_no_governor_objects() -> None:
    engine = make_engine(SchedulerConfig())
    decision = engine.admit(req())
    assert decision.outcome == "admit"
    assert decision.rule == "none"
    assert engine.inflight.depth("ancora-cpu") == 1


def test_completion_releases_the_inflight_slot() -> None:
    engine = make_engine(SchedulerConfig())
    engine.admit(req())
    assert engine.inflight.depth("ancora-cpu") == 1
    assert engine.complete(run_id="wf-1", node_id="n1", usd=0.25) is True
    assert engine.inflight.depth("ancora-cpu") == 0
    assert engine.ledger.run_spend("wf-1") == pytest.approx(0.25)


# --------------------------------------------------------------------------- #
# Rate limiting (AN-040) — scenario 9: no 429 storm
# --------------------------------------------------------------------------- #
def test_rate_limit_defers_a_burst_and_recovers_with_time() -> None:
    clock = FakeClock()
    config = SchedulerConfig(rate_limits={"gemini": RateLimitRule(rps=2.0, burst=3.0)})
    engine = make_engine(config, clock)

    outcomes = [engine.admit(req(node_id=f"n{i}", provider="gemini")).outcome for i in range(5)]
    # The bucket holds 3; the 4th and 5th concurrent calls must wait rather than
    # hit the provider and earn a 429.
    assert outcomes == ["admit", "admit", "admit", "defer", "defer"]

    deferred = engine.admit(req(node_id="n9", provider="gemini"))
    assert deferred.rule == "rate_limit"
    assert 0 < deferred.retry_after <= 0.5  # 1 token at 2 rps

    clock.advance(1.0)  # refills two tokens
    assert engine.admit(req(node_id="n10", provider="gemini")).outcome == "admit"


def test_rate_limit_keys_prefer_the_most_specific_rule() -> None:
    config = SchedulerConfig(
        rate_limits={
            "default": RateLimitRule(rps=100.0, burst=100.0),
            "gemini": RateLimitRule(rps=50.0, burst=50.0),
            "gemini/flash": RateLimitRule(rps=1.0, burst=1.0),
        }
    )
    engine = make_engine(config)
    assert engine.admit(req(provider="gemini", model="flash")).outcome == "admit"
    # The model-specific bucket is exhausted even though the provider bucket is not.
    assert engine.admit(req(node_id="n2", provider="gemini", model="flash")).outcome == "defer"
    assert engine.admit(req(node_id="n3", provider="gemini", model="pro")).outcome == "admit"


def test_unbucketed_providers_are_never_rate_limited() -> None:
    engine = make_engine(SchedulerConfig(rate_limits={"gemini": RateLimitRule(rps=1, burst=1)}))
    for i in range(20):
        assert engine.admit(req(node_id=f"n{i}", provider=None)).outcome == "admit"


# --------------------------------------------------------------------------- #
# Backpressure (AN-041)
# --------------------------------------------------------------------------- #
def test_backpressure_sheds_bulk_first_then_everything() -> None:
    watermark = Watermark(soft=2, hard=4)
    below = evaluate_backpressure(
        depth=1,
        soft=2,
        hard=4,
        priority=5,
        priority_cutoff=1,
        backoff_seconds=1.0,
        max_backoff_seconds=30.0,
    )
    assert below.admitted

    bulk_over_soft = evaluate_backpressure(
        depth=3,
        soft=watermark.soft,
        hard=watermark.hard,
        priority=5,
        priority_cutoff=1,
        backoff_seconds=1.0,
        max_backoff_seconds=30.0,
    )
    high_over_soft = evaluate_backpressure(
        depth=3,
        soft=watermark.soft,
        hard=watermark.hard,
        priority=1,
        priority_cutoff=1,
        backoff_seconds=1.0,
        max_backoff_seconds=30.0,
    )
    assert not bulk_over_soft.admitted, "bulk work must yield once past the soft mark"
    assert high_over_soft.admitted, "urgent work keeps flowing between the marks"

    at_hard = evaluate_backpressure(
        depth=4,
        soft=watermark.soft,
        hard=watermark.hard,
        priority=1,
        priority_cutoff=1,
        backoff_seconds=1.0,
        max_backoff_seconds=30.0,
    )
    assert not at_hard.admitted, "past hard, even urgent work waits"


def test_backpressure_backoff_grows_with_overshoot_and_is_capped() -> None:
    mild = evaluate_backpressure(
        depth=11,
        soft=10,
        hard=20,
        priority=5,
        priority_cutoff=1,
        backoff_seconds=1.0,
        max_backoff_seconds=5.0,
    )
    severe = evaluate_backpressure(
        depth=19,
        soft=10,
        hard=20,
        priority=5,
        priority_cutoff=1,
        backoff_seconds=1.0,
        max_backoff_seconds=5.0,
    )
    assert severe.retry_after > mild.retry_after
    assert severe.retry_after <= 5.0


def test_engine_defers_under_synthetic_overload() -> None:
    config = SchedulerConfig(watermarks={"default": Watermark(soft=3, hard=5)})
    engine = make_engine(config)
    outcomes = [engine.admit(req(node_id=f"n{i}", priority=3)).outcome for i in range(8)]
    assert outcomes[:3] == ["admit", "admit", "admit"]
    assert set(outcomes[3:]) == {"defer"}
    # Draining the queue lets work through again — backpressure is not a fuse.
    for i in range(3):
        engine.complete(run_id="wf-1", node_id=f"n{i}")
    assert engine.admit(req(node_id="n99")).outcome == "admit"


# --------------------------------------------------------------------------- #
# Fair queuing (AN-042)
# --------------------------------------------------------------------------- #
def test_two_tenants_share_a_queue_in_proportion_to_weight() -> None:
    clock = FakeClock()
    config = SchedulerConfig(
        tenants={"big": TenantPolicy(weight=3.0), "small": TenantPolicy(weight=1.0)},
        watermarks={"default": Watermark(soft=10_000, hard=20_000)},
    )
    engine = make_engine(config, clock)

    admitted = {"big": 0, "small": 0}
    # Both tenants push continuously against one queue.
    for i in range(400):
        for tenant in ("big", "small"):
            d = engine.admit(req(run_id=f"wf-{tenant}", node_id=f"{tenant}-{i}", tenant=tenant))
            if d.outcome == "admit":
                admitted[tenant] += 1
                engine.complete(run_id=f"wf-{tenant}", node_id=f"{tenant}-{i}", tenant=tenant)

    ratio = admitted["big"] / max(admitted["small"], 1)
    assert 2.5 <= ratio <= 3.5, f"expected ~3:1 throughput, got {ratio:.2f} ({admitted})"


def test_a_single_tenant_is_never_deferred_for_fairness() -> None:
    config = SchedulerConfig(watermarks={"default": Watermark(soft=10_000, hard=20_000)})
    engine = make_engine(config)
    for i in range(200):
        assert engine.admit(req(node_id=f"n{i}", tenant="solo")).outcome == "admit"


def test_a_newcomer_does_not_bank_credit_while_idle() -> None:
    clock = FakeClock()
    fair = FairShare(idle_seconds=30.0, clock=clock)
    for _ in range(50):
        fair.admit("q", "busy", weight=1.0)
    # A tenant arriving now joins at the front of the line, not at zero — otherwise
    # it would monopolize the queue until it caught up.
    decision = fair.admit("q", "newcomer", weight=1.0)
    assert decision.admitted
    assert decision.virtual_time >= decision.min_virtual_time


def test_rate_limited_tenant_is_refunded_its_fair_share_charge() -> None:
    clock = FakeClock()
    config = SchedulerConfig(
        rate_limits={"gemini": RateLimitRule(rps=1.0, burst=1.0)},
        tenants={"a": TenantPolicy(weight=1.0), "b": TenantPolicy(weight=1.0)},
    )
    engine = make_engine(config, clock)

    # Put tenant "a" on the board with a call that needs no provider quota, and
    # let "b" drain the shared bucket.
    engine.admit(req(node_id="a0", tenant="a"))
    engine.admit(req(node_id="warm", tenant="b", provider="gemini"))
    before = engine.fair.snapshot("ancora-cpu")["a"]

    deferred = engine.admit(req(node_id="a1", tenant="a", provider="gemini"))
    after = engine.fair.snapshot("ancora-cpu")["a"]

    assert deferred.rule == "rate_limit"
    # Being blocked by a provider quota must not cost the tenant its place in line.
    assert after == pytest.approx(before)


# --------------------------------------------------------------------------- #
# Budget (AN-045)
# --------------------------------------------------------------------------- #
def test_soft_budget_warns_but_still_admits() -> None:
    config = SchedulerConfig(budget=BudgetConfig(mode="soft", default_run_usd=1.0, warn_at=0.8))
    engine = make_engine(config)
    engine.admit(req())
    engine.complete(run_id="wf-1", node_id="n1", usd=0.95)

    decision = engine.admit(req(node_id="n2"))
    assert decision.outcome == "admit"
    assert decision.warning is not None and "budget" in decision.warning


def test_hard_budget_rejects_rather_than_defers() -> None:
    config = SchedulerConfig(budget=BudgetConfig(mode="hard", default_run_usd=1.0))
    engine = make_engine(config)
    engine.admit(req())
    engine.complete(run_id="wf-1", node_id="n1", usd=1.5)

    decision = engine.admit(req(node_id="n2"))
    # Waiting cannot make money reappear, so this is terminal, not a deferral.
    assert decision.outcome == "reject"
    assert decision.rule == "budget"


def test_budget_counts_the_estimate_so_one_big_call_cannot_slip_under() -> None:
    verdict = check_budget(
        spent_usd=0.9,
        estimated_usd=5.0,
        limit_usd=1.0,
        mode="hard",
        warn_at=0.8,
        scope="run x",
    )
    assert not verdict.allowed


def test_budget_off_ignores_limits_entirely() -> None:
    config = SchedulerConfig(budget=BudgetConfig(mode="off", default_run_usd=0.01))
    engine = make_engine(config)
    engine.admit(req())
    engine.complete(run_id="wf-1", node_id="n1", usd=100.0)
    assert engine.admit(req(node_id="n2")).outcome == "admit"


# --------------------------------------------------------------------------- #
# Deadlines (AN-046)
# --------------------------------------------------------------------------- #
def test_expired_deadline_is_rejected_not_deferred() -> None:
    engine = make_engine(SchedulerConfig())
    decision = engine.admit(req(deadline_seconds=0.0))
    assert decision.outcome == "reject"
    assert decision.rule == "deadline"


def test_live_deadline_becomes_the_attempt_timeout() -> None:
    engine = make_engine(SchedulerConfig())
    decision = engine.admit(req(deadline_seconds=42.0))
    assert decision.outcome == "admit"
    assert decision.timeout_seconds == pytest.approx(42.0)


# --------------------------------------------------------------------------- #
# In-flight leak recovery
# --------------------------------------------------------------------------- #
def test_unreported_work_expires_so_a_dead_worker_cannot_wedge_the_queue() -> None:
    clock = FakeClock()
    tracker = InflightTracker(ttl_seconds=60.0, clock=clock)
    tracker.admit("wf:a", "q")
    assert tracker.depth("q") == 1

    clock.advance(61.0)  # the worker died without reporting
    assert tracker.depth("q") == 0
    assert tracker.counters()["expired"]["q"] == 1


def test_retrying_a_node_reuses_its_slot_rather_than_stacking() -> None:
    engine = make_engine(SchedulerConfig())
    engine.admit(req(node_id="n1", attempt=1))
    engine.admit(req(node_id="n1", attempt=2))
    assert engine.inflight.depth("ancora-cpu") == 1


# --------------------------------------------------------------------------- #
# Governor precedence
# --------------------------------------------------------------------------- #
def test_deadline_beats_backpressure() -> None:
    config = SchedulerConfig(watermarks={"default": Watermark(soft=0, hard=0)})
    engine = make_engine(config)
    decision = engine.admit(req(deadline_seconds=-1.0))
    assert decision.rule == "deadline"


def test_backpressure_beats_rate_limit() -> None:
    config = SchedulerConfig(
        watermarks={"default": Watermark(soft=0, hard=0)},
        rate_limits={"gemini": RateLimitRule(rps=1.0, burst=1.0)},
    )
    engine = make_engine(config)
    decision = engine.admit(req(provider="gemini"))
    assert decision.rule == "backpressure"
    # The rate-limit bucket must be untouched — we never got that far.
    engine.store.config = SchedulerConfig(rate_limits=config.rate_limits)
    assert engine.admit(req(node_id="n2", provider="gemini")).outcome == "admit"


def test_fairness_can_be_disabled() -> None:
    config = SchedulerConfig(
        fairness=FairnessConfig(enabled=False),
        tenants={"a": TenantPolicy(weight=1.0), "b": TenantPolicy(weight=1.0)},
        watermarks={"default": Watermark(soft=10_000, hard=20_000)},
    )
    engine = make_engine(config)
    for i in range(50):
        assert engine.admit(req(node_id=f"a{i}", tenant="a")).outcome == "admit"
