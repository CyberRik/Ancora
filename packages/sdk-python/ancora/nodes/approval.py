"""ApprovalGate node (AN-055).

Unlike the other built-ins, an approval gate does no I/O — it is a *durable wait*
for a human decision. The wait happens in workflow code (``Workflow.approval``),
using a Temporal signal plus an optional expiry timer, so it consumes zero compute
while parked and survives worker restarts.

This module registers the gate in the node catalog for discovery and defines its
I/O schema; the runtime behaviour is in :mod:`ancora.base`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ancora.nodes.base import Node, NodeContext, NodeError, ResourceHint
from ancora.nodes.registry import register


class ApprovalInput(BaseModel):
    gate_id: str
    prompt: str = ""
    payload: dict[str, object] = Field(default_factory=dict)
    # None = wait indefinitely; otherwise take the timeout branch after N seconds.
    timeout_seconds: float | None = None


class ApprovalOutput(BaseModel):
    gate_id: str
    approved: bool
    comment: str = ""
    decided_by: str | None = None
    timed_out: bool = False


@register
class ApprovalGate(Node):
    """A durable human-approval gate. Resolved by a signal, not an activity."""

    type_name = "approval"
    version = "1.0.0"
    summary = "Durably wait for a human decision via signal, with optional expiry."
    input_model = ApprovalInput
    output_model = ApprovalOutput
    resources = ResourceHint(num_cpus=0.0)
    idempotent = True

    async def execute(self, inp: ApprovalInput, ctx: NodeContext) -> ApprovalOutput:
        # An approval gate is orchestration, not activity work: it must run in the
        # workflow so the wait is durable. Dispatching it as an activity is a bug.
        raise NodeError(
            "ApprovalGate is resolved in workflow code (Workflow.approval), not as an activity",
            transient=False,
        )
