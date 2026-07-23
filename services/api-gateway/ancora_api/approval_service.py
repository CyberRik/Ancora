"""Approval inbox and decision delivery (AN-063, AN-064).

The split here is the important part. The ``approval_gate`` table is an **index**
— it makes "what is waiting on me?" a query. The **decision** is not stored here:
approving a gate sends a Temporal signal, and the workflow's history is what
actually resolves the wait.

Consequences, both deliberate:

* Deciding a gate whose projection row is stale still works — the signal is
  addressed to the workflow, not to the row.
* The row is updated only as a courtesy so the inbox reflects reality
  immediately; the workflow also closes it out on resume. Both writes are
  idempotent, so whichever lands first, the end state is the same.

If the index and Temporal ever disagree, Temporal is right.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from temporalio.client import Client

from ancora_api.schemas import ApprovalDecisionIn, ApprovalOut
from ancora_common.db import session_scope
from ancora_common.models import ApprovalGate, WorkflowRun


class ApprovalNotFoundError(Exception):
    """Raised when a gate id does not exist."""


def _to_out(gate: ApprovalGate, run_id: uuid.UUID | None) -> ApprovalOut:
    return ApprovalOut(
        id=gate.id,
        run_id=run_id,
        temporal_wf_id=gate.temporal_wf_id,
        gate_id=gate.gate_id,
        workflow_name=gate.workflow_name,
        status=gate.status,
        prompt=gate.prompt,
        payload=gate.payload,
        requested_at=gate.requested_at,
        expires_at=gate.expires_at,
        decided_at=gate.decided_at,
        decided_by=gate.decided_by,
        comment=gate.comment,
    )


class ApprovalService:
    def __init__(self, client: Client) -> None:
        self.client = client

    async def list_gates(
        self, status: str | None = "waiting", limit: int = 100
    ) -> list[ApprovalOut]:
        async with session_scope() as session:
            stmt = select(ApprovalGate).order_by(ApprovalGate.requested_at.desc()).limit(limit)
            if status and status != "all":
                stmt = stmt.where(ApprovalGate.status == status)
            gates = list((await session.execute(stmt)).scalars().all())
            # One lookup for the whole page so the inbox can deep-link to runs.
            wf_ids = {g.temporal_wf_id for g in gates}
            runs: dict[str, uuid.UUID] = {}
            if wf_ids:
                rows = await session.execute(
                    select(WorkflowRun.temporal_wf_id, WorkflowRun.id).where(
                        WorkflowRun.temporal_wf_id.in_(wf_ids)
                    )
                )
                runs = dict(rows.all())  # type: ignore[arg-type]
            return [_to_out(g, runs.get(g.temporal_wf_id)) for g in gates]

    async def decide(self, gate_pk: uuid.UUID, decision: ApprovalDecisionIn) -> ApprovalOut:
        """Signal the workflow, then reflect the outcome in the index."""
        async with session_scope() as session:
            gate = (
                await session.execute(select(ApprovalGate).where(ApprovalGate.id == gate_pk))
            ).scalar_one_or_none()
            if gate is None:
                raise ApprovalNotFoundError(f"approval gate '{gate_pk}' not found")
            wf_id = gate.temporal_wf_id
            gate_id = gate.gate_id

        # The signal is the decision. If this raises, nothing is marked decided —
        # an inbox row that says "approved" for a workflow that never heard the
        # signal would be a lie the UI has no way to detect.
        handle = self.client.get_workflow_handle(wf_id)
        await handle.signal(
            "submit_decision",
            {
                "gate_id": gate_id,
                "approved": decision.approved,
                "comment": decision.comment,
                "decided_by": decision.decided_by,
            },
        )

        async with session_scope() as session:
            gate = (
                await session.execute(select(ApprovalGate).where(ApprovalGate.id == gate_pk))
            ).scalar_one()
            gate.status = "approved" if decision.approved else "rejected"
            gate.decided_at = datetime.now(UTC)
            gate.decided_by = decision.decided_by
            gate.comment = decision.comment or None
            run_row = (
                await session.execute(
                    select(WorkflowRun.id).where(WorkflowRun.temporal_wf_id == wf_id)
                )
            ).scalar_one_or_none()
            return _to_out(gate, run_row)
