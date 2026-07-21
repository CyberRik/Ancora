"""Workflow catalog endpoints (AN-020, read side).

Definitions are registered by the workers on startup (they own the code); the API
exposes the catalog for discovery and for starting runs by name.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ancora_api.deps import get_service
from ancora_api.schemas import WorkflowDefOut
from ancora_api.service import WorkflowService

router = APIRouter(prefix="/v1", tags=["workflows"])


@router.get("/workflows", response_model=list[WorkflowDefOut])
async def list_workflows(
    service: WorkflowService = Depends(get_service),
) -> list[WorkflowDefOut]:
    return await service.list_defs()


@router.get("/workflows/{name}", response_model=WorkflowDefOut)
async def get_workflow(
    name: str,
    service: WorkflowService = Depends(get_service),
) -> WorkflowDefOut:
    defs = await service.list_defs()
    for d in defs:
        if d.name == name:
            return d
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"workflow '{name}' not found"
    )
