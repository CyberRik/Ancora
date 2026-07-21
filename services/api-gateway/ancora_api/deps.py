"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

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
