"""Cost governor (AN-045).

A runaway agent loop is not a correctness bug — every step succeeds — so nothing
in the durability machinery stops it. Only money does. The governor checks
projected spend at admission time and, in ``hard`` mode, refuses to start work
that would exceed a run's or a tenant's budget.

The MVP ships ``soft`` mode by default: admissions are never blocked, but once
spend crosses ``warn_at`` the decision carries a warning that surfaces in the API
response, the metrics, and the run's UI. That is deliberate — a budget governor
that starts rejecting on day one, calibrated from guesses, destroys more work
than it saves. The interface is the same in both modes, so switching to ``hard``
is a config change, not a code change.

Spend is reported by the activity worker after each node completes (the same
number that lands in ``cost_ledger``), so the governor's view matches the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BudgetVerdict:
    allowed: bool
    # Set when spend has crossed warn_at but the request is still allowed.
    warning: str | None = None
    reason: str | None = None
    spent_usd: float = 0.0
    limit_usd: float | None = None

    @property
    def fraction(self) -> float:
        if not self.limit_usd:
            return 0.0
        return self.spent_usd / self.limit_usd


@dataclass
class BudgetLedger:
    """Running spend per run and per tenant, fed by worker completion reports."""

    _by_run: dict[str, float] = field(default_factory=dict)
    _by_tenant: dict[str, float] = field(default_factory=dict)

    def record(self, *, run_id: str, tenant: str, usd: float) -> None:
        if usd <= 0:
            return
        self._by_run[run_id] = self._by_run.get(run_id, 0.0) + usd
        self._by_tenant[tenant] = self._by_tenant.get(tenant, 0.0) + usd

    def run_spend(self, run_id: str) -> float:
        return self._by_run.get(run_id, 0.0)

    def tenant_spend(self, tenant: str) -> float:
        return self._by_tenant.get(tenant, 0.0)

    def forget_run(self, run_id: str) -> None:
        """Drop a finished run's row (the ledger table keeps the durable record)."""
        self._by_run.pop(run_id, None)

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {"runs": dict(self._by_run), "tenants": dict(self._by_tenant)}

    def reset(self) -> None:
        self._by_run.clear()
        self._by_tenant.clear()


def check_budget(
    *,
    spent_usd: float,
    estimated_usd: float,
    limit_usd: float | None,
    mode: str,
    warn_at: float,
    scope: str,
) -> BudgetVerdict:
    """Pure budget rule for one scope (run or tenant).

    ``estimated_usd`` is the caller's estimate of what this node will cost; it is
    included so a single large call cannot slip under the wire at 99% of budget.
    """
    if mode == "off" or limit_usd is None or limit_usd <= 0:
        return BudgetVerdict(allowed=True, spent_usd=spent_usd, limit_usd=limit_usd)

    projected = spent_usd + max(estimated_usd, 0.0)
    if projected > limit_usd:
        message = (
            f"{scope} budget exceeded: ${projected:.4f} projected against a "
            f"${limit_usd:.2f} limit (${spent_usd:.4f} already spent)"
        )
        if mode == "hard":
            return BudgetVerdict(
                allowed=False, reason=message, spent_usd=spent_usd, limit_usd=limit_usd
            )
        return BudgetVerdict(
            allowed=True, warning=message, spent_usd=spent_usd, limit_usd=limit_usd
        )

    if projected >= limit_usd * warn_at:
        return BudgetVerdict(
            allowed=True,
            warning=(
                f"{scope} budget at {projected / limit_usd:.0%} "
                f"(${projected:.4f} of ${limit_usd:.2f})"
            ),
            spent_usd=spent_usd,
            limit_usd=limit_usd,
        )
    return BudgetVerdict(allowed=True, spent_usd=spent_usd, limit_usd=limit_usd)
