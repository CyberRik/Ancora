"""Per-provider rate-limit governor via token buckets (AN-040).

The scheduler's Admit path consults a token bucket keyed by ``provider/model``
before letting an LLM/HTTP call proceed. When the bucket is empty the call is
**deferred** with a ``retry_after`` (Temporal holds the work and retries), so a
burst of concurrent calls smooths to the configured rate instead of triggering a
provider 429 storm (scenario 9).

The refill arithmetic is a pure function (:func:`refill_and_take`) so it is unit
-testable without any clock or store. Two backends share it: an in-process
:class:`InMemoryRateLimiter` (tests, single-worker dev) and a
:class:`RedisRateLimiter` (cluster-wide, atomic via a Lua script).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

# A monotonic clock callable, injectable for deterministic tests.
Clock = Callable[[], float]


@dataclass(frozen=True)
class RateLimitConfig:
    """A bucket's shape. ``rps`` is the sustained refill; ``burst`` the capacity."""

    rps: float
    burst: float

    def __post_init__(self) -> None:
        if self.rps <= 0 or self.burst <= 0:
            raise ValueError("rate-limit rps and burst must be positive")


@dataclass(frozen=True)
class AdmitDecision:
    admitted: bool
    retry_after: float  # seconds until enough tokens exist (0 when admitted)


def refill_and_take(
    *,
    tokens: float,
    last_ts: float,
    now: float,
    cfg: RateLimitConfig,
    want: float = 1.0,
) -> tuple[float, float, AdmitDecision]:
    """Advance a bucket to ``now`` and try to take ``want`` tokens.

    Returns ``(new_tokens, new_ts, decision)``. Pure — no clock, no I/O — so the
    same logic runs in-memory and inside the Redis Lua script.
    """
    elapsed = max(0.0, now - last_ts)
    available = min(cfg.burst, tokens + elapsed * cfg.rps)
    if available >= want:
        return available - want, now, AdmitDecision(admitted=True, retry_after=0.0)
    deficit = want - available
    retry_after = deficit / cfg.rps
    return available, now, AdmitDecision(admitted=False, retry_after=retry_after)


class InMemoryRateLimiter:
    """Single-process token-bucket limiter (tests + single-worker dev)."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._state: dict[str, tuple[float, float]] = {}
        self._clock = clock or time.monotonic

    def admit(self, key: str, cfg: RateLimitConfig, *, want: float = 1.0) -> AdmitDecision:
        now = self._clock()
        tokens, last_ts = self._state.get(key, (cfg.burst, now))
        new_tokens, new_ts, decision = refill_and_take(
            tokens=tokens, last_ts=last_ts, now=now, cfg=cfg, want=want
        )
        self._state[key] = (new_tokens, new_ts)
        return decision


# --------------------------------------------------------------------------- #
# Redis backend — atomic, cluster-wide.
# --------------------------------------------------------------------------- #
# KEYS[1] = bucket key; ARGV = rps, burst, now, want. Returns {admitted, retry_after, tokens}.
_LUA_TOKEN_BUCKET = """
local key = KEYS[1]
local rps = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local want = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then tokens = burst end
if ts == nil then ts = now end

local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
local available = math.min(burst, tokens + elapsed * rps)

local admitted = 0
local retry_after = 0
if available >= want then
  available = available - want
  admitted = 1
else
  retry_after = (want - available) / rps
end

redis.call('HSET', key, 'tokens', available, 'ts', now)
-- expire idle buckets so keys don't accumulate forever
redis.call('PEXPIRE', key, math.ceil((burst / rps) * 1000) + 1000)
return {admitted, tostring(retry_after)}
"""


class RedisRateLimiter:
    """Cluster-wide token-bucket limiter backed by Redis (atomic Lua)."""

    def __init__(self, redis: object, *, namespace: str = "ancora:rl") -> None:
        # ``redis`` is a redis.asyncio.Redis; typed as object to avoid a hard dep here.
        self._redis = redis
        self._namespace = namespace

    async def admit(self, key: str, cfg: RateLimitConfig, *, want: float = 1.0) -> AdmitDecision:
        now = time.time()
        full_key = f"{self._namespace}:{key}"
        result = await self._redis.eval(  # type: ignore[attr-defined]
            _LUA_TOKEN_BUCKET, 1, full_key, cfg.rps, cfg.burst, now, want
        )
        admitted = bool(int(result[0]))
        retry_after = float(result[1])
        return AdmitDecision(admitted=admitted, retry_after=retry_after)
