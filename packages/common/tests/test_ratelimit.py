"""Unit tests for the token-bucket rate governor (AN-040)."""

from __future__ import annotations

import pytest

from ancora_common.ratelimit import (
    AdmitDecision,
    InMemoryRateLimiter,
    RateLimitConfig,
    refill_and_take,
)


def test_config_rejects_nonpositive() -> None:
    with pytest.raises(ValueError):
        RateLimitConfig(rps=0, burst=1)
    with pytest.raises(ValueError):
        RateLimitConfig(rps=1, burst=0)


def test_refill_admits_until_burst_exhausted() -> None:
    cfg = RateLimitConfig(rps=1, burst=3)
    tokens, ts = 3.0, 100.0
    # Three immediate takes at the same instant drain the burst.
    for _ in range(3):
        tokens, ts, decision = refill_and_take(tokens=tokens, last_ts=ts, now=100.0, cfg=cfg)
        assert decision.admitted
    tokens, ts, decision = refill_and_take(tokens=tokens, last_ts=ts, now=100.0, cfg=cfg)
    assert not decision.admitted
    assert decision.retry_after == pytest.approx(1.0)  # 1 token / 1 rps


def test_refill_recovers_over_time() -> None:
    cfg = RateLimitConfig(rps=2, burst=2)
    # Drain, then advance 1s → 2 tokens refill.
    tokens, ts, _ = refill_and_take(tokens=2.0, last_ts=0.0, now=0.0, cfg=cfg, want=2.0)
    _, _, decision = refill_and_take(tokens=tokens, last_ts=ts, now=1.0, cfg=cfg, want=2.0)
    assert decision.admitted


def test_inmemory_limiter_smooths_a_burst() -> None:
    now = [0.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0])
    cfg = RateLimitConfig(rps=5, burst=5)

    # A burst of 10 concurrent calls at t=0: exactly `burst` admitted.
    admits = [limiter.admit("openai/gpt", cfg) for _ in range(10)]
    admitted = sum(1 for d in admits if d.admitted)
    assert admitted == 5
    deferred = [d for d in admits if not d.admitted]
    assert deferred and all(d.retry_after > 0 for d in deferred)

    # Independent buckets don't interfere.
    other = limiter.admit("anthropic/claude", cfg)
    assert other.admitted


def test_inmemory_limiter_defers_then_recovers() -> None:
    now = [0.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0])
    cfg = RateLimitConfig(rps=1, burst=1)

    assert limiter.admit("k", cfg).admitted
    d = limiter.admit("k", cfg)
    assert not d.admitted and d.retry_after == pytest.approx(1.0)

    now[0] = 1.0  # one token refills
    assert limiter.admit("k", cfg).admitted


def test_admit_decision_shape() -> None:
    d = AdmitDecision(admitted=True, retry_after=0.0)
    assert d.admitted and d.retry_after == 0.0
