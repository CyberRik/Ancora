"""Run lifecycle endpoints (AN-018)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query, status

from ancora_api.deps import get_service
from ancora_api.schemas import (
    RunLinks,
    RunLiveOut,
    RunOut,
    StartRunRequest,
    StartRunResponse,
)
from ancora_api.service import WorkflowService

router = APIRouter(prefix="/v1", tags=["runs"])


@router.post(
    "/workflows/{name}/runs",
    response_model=StartRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_run(
    name: str,
    req: StartRunRequest,
    service: WorkflowService = Depends(get_service),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StartRunResponse:
    run = await service.start_run(name, req, idempotency_key)
    return StartRunResponse(
        run_id=run.id,
        temporal_wf_id=run.temporal_wf_id,
        status=run.status,
        links=RunLinks(
            self=f"/v1/runs/{run.id}",
            stream=f"/v1/stream/runs/{run.id}",
        ),
    )


@router.get("/runs", response_model=list[RunOut])
async def list_runs(
    service: WorkflowService = Depends(get_service),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[RunOut]:
    return await service.list_runs(limit=limit)


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(
    run_id: uuid.UUID,
    service: WorkflowService = Depends(get_service),
) -> RunOut:
    return await service.get_run(run_id)


@router.get("/runs/{run_id}/activities", response_model=RunLiveOut)
async def get_run_activities(
    run_id: uuid.UUID,
    service: WorkflowService = Depends(get_service),
) -> RunLiveOut:
    """Live per-activity state (real attempt counter + failure) for the demo view."""
    return await service.get_run_live(run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
async def cancel_run(
    run_id: uuid.UUID,
    service: WorkflowService = Depends(get_service),
) -> RunOut:
    return await service.cancel_run(run_id)


@router.post("/runs/{run_id}/signals/{name}", response_model=RunOut)
async def signal_run(
    run_id: uuid.UUID,
    name: str,
    service: WorkflowService = Depends(get_service),
    arg: Any = Body(default=None),
) -> RunOut:
    """Advance a durably-waiting workflow (e.g. approve a gate)."""
    return await service.signal_run(run_id, name, arg)
