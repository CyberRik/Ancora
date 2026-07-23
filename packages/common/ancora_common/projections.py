"""Writers for the Phase-3 read models (AN-057, AN-064, AN-044).

Every function here writes a **derived** view. Temporal's history is the record
of what happened; these tables exist so the API can answer "what did this run
cost?" and "what is waiting on me?" in a query instead of a replay.

Two properties follow from that and shape every write below:

* **Best-effort.** A projection write must never fail the activity or workflow
  that triggered it. Losing a ledger row costs a reporting inaccuracy; failing
  the node costs real work. Every function swallows and logs.
* **Idempotent.** Activities are at-least-once, so the same node can report the
  same attempt twice. Cost rows upsert on ``(wf_id, node_id, attempt)`` and gate
  rows upsert on ``(wf_id, gate_id)``, so a duplicate report is absorbed rather
  than double-counted — the same discipline as the idempotency inbox, applied to
  reporting.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ancora_common.db import session_scope
from ancora_common.models import ApprovalGate, CostLedger, RetryAttempt

logger = logging.getLogger("ancora.projections")

# Projections are pure reporting, so they are switchable. Tests that run without
# a database turn them off rather than paying a connection failure per node.
_enabled = True


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def record_cost(
    *,
    temporal_wf_id: str,
    node_id: str,
    node_type: str,
    attempt: int,
    cost: dict[str, Any],
) -> None:
    """Append one node execution's cost to the ledger (AN-056, AN-057)."""
    if not _enabled:
        return
    usd = float(cost.get("usd", 0.0) or 0.0)
    input_tokens = int(cost.get("input_tokens", 0) or 0)
    output_tokens = int(cost.get("output_tokens", 0) or 0)
    gpu_seconds = float(cost.get("gpu_seconds", 0.0) or 0.0)
    if not (usd or input_tokens or output_tokens or gpu_seconds):
        # Free nodes (approval gates, cache hits) would only add noise.
        return
    try:
        async with session_scope() as session:
            stmt = (
                pg_insert(CostLedger)
                .values(
                    temporal_wf_id=temporal_wf_id,
                    node_id=node_id,
                    node_type=node_type,
                    attempt=attempt,
                    provider=cost.get("provider"),
                    model=cost.get("model"),
                    usd=usd,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    gpu_seconds=gpu_seconds,
                )
                # A re-reported attempt is the same execution, not a second one.
                .on_conflict_do_nothing(constraint="uq_cost_ledger_node_attempt")
            )
            await session.execute(stmt)
    except Exception as exc:  # noqa: BLE001 — reporting must never fail the node
        logger.warning("cost ledger write failed for %s/%s: %s", temporal_wf_id, node_id, exc)


async def record_retry(
    *,
    temporal_wf_id: str,
    node_id: str,
    node_type: str,
    attempt: int,
    error: str,
    transient: bool,
    retry_after: float | None = None,
    worker_id: str | None = None,
) -> None:
    """Log a failed attempt and the transient/terminal call the runtime made."""
    if not _enabled:
        return
    try:
        async with session_scope() as session:
            session.add(
                RetryAttempt(
                    temporal_wf_id=temporal_wf_id,
                    node_id=node_id,
                    node_type=node_type,
                    attempt=attempt,
                    error=error[:4000],
                    transient=transient,
                    retry_after_seconds=retry_after,
                    worker_id=worker_id,
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("retry log write failed for %s/%s: %s", temporal_wf_id, node_id, exc)


async def open_gate(
    *,
    temporal_wf_id: str,
    gate_id: str,
    workflow_name: str | None = None,
    prompt: str | None = None,
    payload: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Index a gate a workflow has just parked at (AN-064).

    Re-opening an existing gate resets it to ``waiting``: a workflow that replays
    past its gate is genuinely waiting again, and the inbox should say so.
    """
    if not _enabled:
        return
    try:
        async with session_scope() as session:
            stmt = (
                pg_insert(ApprovalGate)
                .values(
                    temporal_wf_id=temporal_wf_id,
                    gate_id=gate_id,
                    workflow_name=workflow_name,
                    status="waiting",
                    prompt=prompt,
                    payload=payload,
                    expires_at=expires_at,
                )
                .on_conflict_do_update(
                    constraint="uq_approval_gate_wf_gate",
                    set_={
                        "status": "waiting",
                        "prompt": prompt,
                        "payload": payload,
                        "expires_at": expires_at,
                        "decided_at": None,
                        "decided_by": None,
                        "comment": None,
                    },
                )
            )
            await session.execute(stmt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("approval gate open failed for %s/%s: %s", temporal_wf_id, gate_id, exc)


async def close_gate(
    *,
    temporal_wf_id: str,
    gate_id: str,
    approved: bool,
    timed_out: bool = False,
    comment: str | None = None,
    decided_by: str | None = None,
) -> None:
    """Mark a gate resolved once the workflow has resumed past it."""
    if not _enabled:
        return
    status = "expired" if timed_out else ("approved" if approved else "rejected")
    try:
        async with session_scope() as session:
            result = await session.execute(
                select(ApprovalGate).where(
                    ApprovalGate.temporal_wf_id == temporal_wf_id,
                    ApprovalGate.gate_id == gate_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                # The open never landed (DB was down); record the outcome anyway so
                # the gate does not silently vanish from history.
                session.add(
                    ApprovalGate(
                        temporal_wf_id=temporal_wf_id,
                        gate_id=gate_id,
                        status=status,
                        decided_at=_utcnow(),
                        decided_by=decided_by,
                        comment=comment,
                    )
                )
                return
            row.status = status
            row.decided_at = _utcnow()
            row.decided_by = decided_by
            row.comment = comment
    except Exception as exc:  # noqa: BLE001
        logger.warning("approval gate close failed for %s/%s: %s", temporal_wf_id, gate_id, exc)
