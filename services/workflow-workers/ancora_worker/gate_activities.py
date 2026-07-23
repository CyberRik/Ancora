"""Activities that maintain the approval-gate read model (AN-064).

``Workflow.approval`` calls these by name when it parks at and resumes past a
gate. They live on the workflow worker rather than the activity workers because
they are pure bookkeeping against Ancora's own database — no provider, no GPU, no
capability routing — and keeping them on the orchestration queue means a gate can
be indexed even when every activity worker is busy or down.

The workflow id comes from ``activity.info()``, not from the argument: the
projection must key off the run that actually parked, and a workflow cannot be
allowed to index a gate against someone else's run.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity

from ancora_common import projections


@activity.defn(name="open_approval_gate")
async def open_approval_gate(arg: dict[str, Any]) -> dict[str, Any]:
    """Record that a run is now waiting on ``gate_id``."""
    info = activity.info()
    gate_id = str(arg.get("gate_id", ""))
    await projections.open_gate(
        temporal_wf_id=str(info.workflow_id),
        gate_id=gate_id,
        workflow_name=arg.get("workflow_name"),
        prompt=arg.get("prompt") or None,
        payload=arg.get("payload") or None,
    )
    return {"gate_id": gate_id, "status": "waiting"}


@activity.defn(name="close_approval_gate")
async def close_approval_gate(arg: dict[str, Any]) -> dict[str, Any]:
    """Record the decision that released a gate (or its expiry)."""
    info = activity.info()
    gate_id = str(arg.get("gate_id", ""))
    approved = bool(arg.get("approved", False))
    timed_out = bool(arg.get("timed_out", False))
    await projections.close_gate(
        temporal_wf_id=str(info.workflow_id),
        gate_id=gate_id,
        approved=approved,
        timed_out=timed_out,
        comment=arg.get("comment") or None,
        decided_by=arg.get("decided_by") or None,
    )
    status = "expired" if timed_out else ("approved" if approved else "rejected")
    return {"gate_id": gate_id, "status": status}


GATE_ACTIVITIES: list[Any] = [open_approval_gate, close_approval_gate]
