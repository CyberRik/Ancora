"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from ancora_api.approval_service import ApprovalService
from ancora_api.chaos import ChaosLog, ChaosService
from ancora_api.cost_service import CostService
from ancora_api.service import WorkflowService
from ancora_api.settings import get_settings
from ancora_api.worker_service import WorkerService

# Injection history lives in the process: it is a demo aid, not a record. A
# restarted API forgets it, which is fine — the runs it acted on do not.
_chaos_log = ChaosLog()

# One Redis client for the process. The recovery view polls liveness every couple
# of seconds while a demo is running, and a per-request client would churn
# connections for no benefit.
_worker_service: WorkerService | None = None


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


def get_worker_service() -> WorkerService:
    """The worker registry read model, shared process-wide (see ``_worker_service``)."""
    global _worker_service
    if _worker_service is None:
        _worker_service = WorkerService(get_settings().redis_url)
    return _worker_service


def get_chaos_service() -> ChaosService:
    """Chaos needs neither Temporal nor the DB — it talks to the Docker daemon."""
    settings = get_settings()
    return ChaosService(
        enabled=settings.chaos_enabled,
        socket_path=settings.docker_socket,
        project=settings.compose_project,
        log=_chaos_log,
    )
