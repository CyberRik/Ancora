"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from ancora_api.approval_service import ApprovalService
from ancora_api.cost_service import CostService
from ancora_api.service import WorkflowService


def get_service(request: Request) -> WorkflowService:
    """Provide a WorkflowService bound to the app's Temporal client.

    Returns 503 if Temporal was not reachable at startup — the control plane is
    up but cannot orchestrate until Temporal is available.
    """
    client = getattr(request.app.state, "temporal_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Temporal is not connected",
        )
    return WorkflowService(client)


def get_approval_service(request: Request) -> ApprovalService:
    """Approvals need Temporal: the decision is a signal, not a database write."""
    client = getattr(request.app.state, "temporal_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Temporal is not connected",
        )
    return ApprovalService(client)


def get_cost_service() -> CostService:
    """Cost reads hit only the projections, so they work with Temporal down."""
    return CostService()
