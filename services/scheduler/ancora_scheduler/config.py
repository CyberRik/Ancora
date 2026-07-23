"""Declarative scheduler policy and its hot-reloading loader (AN-048).

Everything the admission engine enforces — provider rate limits, queue
watermarks, tenant weights, budgets, priority lanes — is data, not code. That
matters operationally: when a provider tightens its quota at 3am you edit a file,
not a deployment. :class:`ConfigStore` reloads on mtime change and **keeps the
last valid config** if the new one fails validation, so a typo degrades to "the
change didn't take" rather than "the scheduler stopped admitting work".

The file may be JSON or YAML (YAML needs ``pyyaml``, which the service depends on).
Unknown keys are rejected rather than ignored: a misspelled ``watermarks`` that
silently does nothing is worse than a startup error.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger("ancora.scheduler.config")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RateLimitRule(_Strict):
    """A token bucket for one provider (or provider/model) key."""

    rps: float = Field(gt=0, description="Sustained requests per second.")
    burst: float = Field(gt=0, description="Bucket capacity — the tolerated burst.")

    @field_validator("burst")
    @classmethod
    def _burst_at_least_one(cls, v: float) -> float:
        if v < 1:
            raise ValueError("burst must allow at least one request")
        return v


class Watermark(_Strict):
    """Queue-depth thresholds that trigger backpressure (AN-041).

    Below ``soft`` everything is admitted. Between ``soft`` and ``hard`` only
    high-priority work is admitted and the rest is deferred — the queue drains
    while urgent work still flows. At or above ``hard`` everything defers.
    """

    soft: int = Field(ge=0, default=50)
    hard: int = Field(ge=0, default=200)
    # How long a deferred caller is told to wait at the soft threshold. Scales
    # with overshoot up to ``max_backoff_seconds``.
    backoff_seconds: float = Field(gt=0, default=1.0)
    max_backoff_seconds: float = Field(gt=0, default=30.0)

    @field_validator("hard")
    @classmethod
    def _ordered(cls, v: int, info: Any) -> int:
        soft = info.data.get("soft")
        if soft is not None and v < soft:
            raise ValueError("hard watermark must be >= soft watermark")
        return v


class TenantPolicy(_Strict):
    """Per-tenant fair-share weight and budget (AN-042, AN-045)."""

    # Relative share of a contended queue. Weight 3 gets 3x the throughput of
    # weight 1 while both are saturating.
    weight: float = Field(gt=0, default=1.0)
    # None = unlimited. Enforced per the top-level ``budget_mode``.
    budget_usd: float | None = Field(default=None, ge=0)


class FairnessConfig(_Strict):
    """Weighted fair queuing knobs (AN-042)."""

    enabled: bool = True
    # A tenant is dropped from the active set after this long without a request,
    # so an idle tenant's virtual time cannot bank credit forever.
    idle_seconds: float = Field(gt=0, default=30.0)
    # How long a tenant that is running ahead of its share is told to wait.
    defer_seconds: float = Field(gt=0, default=0.25)


class BudgetConfig(_Strict):
    """Cost governor (AN-045). ``soft`` warns and admits; ``hard`` rejects."""

    mode: str = "soft"
    # Applies to every run that has no tenant-specific budget.
    default_run_usd: float | None = Field(default=None, ge=0)
    # Fraction of budget at which a warning is attached to admissions.
    warn_at: float = Field(gt=0, le=1.0, default=0.8)

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("soft", "hard", "off"):
            raise ValueError("budget mode must be one of: soft, hard, off")
        return v


class SchedulerConfig(_Strict):
    """The whole policy document."""

    # Bucket key → rule. Keys are matched most-specific-first:
    # "provider/model" then "provider" then "default".
    rate_limits: dict[str, RateLimitRule] = Field(default_factory=dict)
    # Task queue → watermark. "default" applies to queues without an entry.
    watermarks: dict[str, Watermark] = Field(default_factory=dict)
    tenants: dict[str, TenantPolicy] = Field(default_factory=dict)
    fairness: FairnessConfig = Field(default_factory=FairnessConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    # Priority lane at or below which work is still admitted between the soft and
    # hard watermarks (see ancora.policy: 1=high, 3=normal, 5=bulk).
    backpressure_priority_cutoff: int = Field(ge=1, default=1)
    # How long an admission is assumed in-flight if the worker never reports
    # completion, so a lost report cannot inflate the backlog forever.
    inflight_ttl_seconds: float = Field(gt=0, default=900.0)

    def rate_limit_for(self, provider: str | None, model: str | None) -> RateLimitRule | None:
        """Most-specific matching bucket rule, or ``None`` when unlimited."""
        if provider:
            if model:
                rule = self.rate_limits.get(f"{provider}/{model}")
                if rule is not None:
                    return rule
            rule = self.rate_limits.get(provider)
            if rule is not None:
                return rule
        return self.rate_limits.get("default")

    def watermark_for(self, queue: str) -> Watermark:
        return self.watermarks.get(queue) or self.watermarks.get("default") or Watermark()

    def tenant(self, name: str) -> TenantPolicy:
        return self.tenants.get(name) or self.tenants.get("default") or TenantPolicy()


DEFAULT_CONFIG = SchedulerConfig(
    rate_limits={
        # Conservative defaults that keep the CI mock and a real provider honest
        # without being so tight that the demo visibly stalls.
        "default": RateLimitRule(rps=20.0, burst=40.0),
        "gemini": RateLimitRule(rps=5.0, burst=10.0),
        "mock": RateLimitRule(rps=100.0, burst=200.0),
    },
    watermarks={"default": Watermark(soft=50, hard=200)},
    tenants={"default": TenantPolicy(weight=1.0)},
)


def _parse(path: Path, raw: str) -> dict[str, Any]:
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ValueError("YAML config requires pyyaml; use a .json file instead") from exc
        loaded = yaml.safe_load(raw)
    else:
        loaded = json.loads(raw)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name}: config must be a mapping, got {type(loaded).__name__}")
    return loaded


def load_config(path: Path) -> SchedulerConfig:
    """Read and validate a policy file. Raises ``ValueError`` with a readable message."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read scheduler config {path}: {exc}") from exc
    try:
        return SchedulerConfig.model_validate(_parse(path, raw))
    except ValidationError as exc:
        # Pydantic's default repr is dense; surface the field path and message only.
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ValueError(f"invalid scheduler config {path.name}: {problems}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid scheduler config {path.name}: {exc}") from exc


@dataclass
class ConfigStore:
    """Holds the live config and swaps it in when the file changes.

    ``reload_if_changed`` is called on the request path (it is a stat, not a read,
    unless the mtime moved) so a policy edit takes effect on the next admission
    without a restart or a watcher thread.
    """

    path: Path | None = None
    config: SchedulerConfig = field(default_factory=lambda: DEFAULT_CONFIG)
    _mtime: float | None = None
    # Set when a reload failed validation; surfaced by GET /v1/scheduler/config so
    # an operator can see the running policy is not the one on disk.
    last_error: str | None = None

    @classmethod
    def from_path(cls, path: Path | None) -> ConfigStore:
        store = cls(path=path)
        if path is not None and path.exists():
            store.reload_if_changed(force=True)
        elif path is not None:
            logger.warning("scheduler config %s not found; using built-in defaults", path)
        return store

    def reload_if_changed(self, *, force: bool = False) -> bool:
        """Swap in a new config if the file's mtime moved. Returns True on swap."""
        if self.path is None:
            return False
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return False
        if not force and mtime == self._mtime:
            return False
        try:
            self.config = load_config(self.path)
        except ValueError as exc:
            # Keep serving the last good policy — a bad edit must not open the gates.
            self.last_error = str(exc)
            self._mtime = mtime
            logger.error("scheduler config reload rejected: %s", exc)
            return False
        self._mtime = mtime
        self.last_error = None
        logger.info("scheduler config loaded from %s", self.path)
        return True
