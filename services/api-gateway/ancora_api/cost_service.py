"""Read model for run cost and retry history (AN-057, AN-044).

Both views read the Phase-3 projections rather than Temporal history. That is the
point of having them: "what did this run cost, sliced by model" is a `GROUP BY`,
not a replay of every event in every workflow.

Rollups are computed in Python rather than SQL. A run's ledger is tens of rows,
not millions, and doing it here keeps the three slices (node / model / provider)
consistent with each other and with the line items in one pass — no risk of the
totals disagreeing with the breakdown because one query rounded differently.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select

from ancora_api.schemas import CostGroupOut, CostLineOut, RetryAttemptOut, RunCostOut
from ancora_common.db import session_scope
from ancora_common.models import CostLedger, RetryAttempt


@dataclass
class _Bucket:
    usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, row: CostLedger) -> None:
        self.usd += float(row.usd)
        self.input_tokens += row.input_tokens
        self.output_tokens += row.output_tokens
        self.calls += 1


def _groups(buckets: dict[str, _Bucket]) -> list[CostGroupOut]:
    """Rollup slices, most expensive first — the order a reader actually wants."""
    return sorted(
        (
            CostGroupOut(
                key=key,
                usd=round(b.usd, 8),
                input_tokens=b.input_tokens,
                output_tokens=b.output_tokens,
                calls=b.calls,
            )
            for key, b in buckets.items()
        ),
        key=lambda g: (-g.usd, g.key),
    )


class CostService:
    """Cost and retry reads for a single run."""

    async def run_cost(self, run_id: uuid.UUID, temporal_wf_id: str) -> RunCostOut:
        async with session_scope() as session:
            result = await session.execute(
                select(CostLedger)
                .where(CostLedger.temporal_wf_id == temporal_wf_id)
                .order_by(CostLedger.created_at)
            )
            rows = list(result.scalars().all())

        by_node: dict[str, _Bucket] = defaultdict(_Bucket)
        by_model: dict[str, _Bucket] = defaultdict(_Bucket)
        by_provider: dict[str, _Bucket] = defaultdict(_Bucket)
        total_usd = 0.0
        input_tokens = 0
        output_tokens = 0
        gpu_seconds = 0.0

        for row in rows:
            by_node[row.node_id].add(row)
            by_model[row.model or "—"].add(row)
            by_provider[row.provider or "—"].add(row)
            total_usd += float(row.usd)
            input_tokens += row.input_tokens
            output_tokens += row.output_tokens
            gpu_seconds += float(row.gpu_seconds)

        return RunCostOut(
            run_id=run_id,
            total_usd=round(total_usd, 8),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            gpu_seconds=round(gpu_seconds, 6),
            by_node=_groups(by_node),
            by_model=_groups(by_model),
            by_provider=_groups(by_provider),
            lines=[
                CostLineOut(
                    node_id=r.node_id,
                    node_type=r.node_type,
                    attempt=r.attempt,
                    provider=r.provider,
                    model=r.model,
                    usd=float(r.usd),
                    input_tokens=r.input_tokens,
                    output_tokens=r.output_tokens,
                    gpu_seconds=float(r.gpu_seconds),
                    created_at=r.created_at,
                )
                for r in rows
            ],
        )

    async def run_retries(self, temporal_wf_id: str) -> list[RetryAttemptOut]:
        async with session_scope() as session:
            result = await session.execute(
                select(RetryAttempt)
                .where(RetryAttempt.temporal_wf_id == temporal_wf_id)
                .order_by(RetryAttempt.created_at)
            )
            rows = list(result.scalars().all())
        return [
            RetryAttemptOut(
                node_id=r.node_id,
                node_type=r.node_type,
                attempt=r.attempt,
                error=r.error,
                transient=r.transient,
                retry_after_seconds=(
                    float(r.retry_after_seconds) if r.retry_after_seconds is not None else None
                ),
                created_at=r.created_at,
            )
            for r in rows
        ]
