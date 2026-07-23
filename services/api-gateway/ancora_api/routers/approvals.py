"""Approval inbox endpoints (AN-063, AN-064)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from ancora_api.approval_service import ApprovalService
from ancora_api.deps import get_approval_service
from ancora_api.schemas import ApprovalDecisionIn, ApprovalOut

router = APIRouter(prefix="/v1", tags=["approvals"])


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    service: ApprovalService = Depends(get_approval_service),
    status: str = Query(
        default="waiting",
        description="waiting | approved | rejected | expired | all",
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ApprovalOut]:
    """Gates awaiting a human. Defaults to the ones that still need a decision."""
    return await service.list_gates(status=status, limit=limit)


@router.post("/approvals/{gate_pk}/decision", response_model=ApprovalOut)
async def decide_approval(
    gate_pk: uuid.UUID,
    decision: ApprovalDecisionIn,
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalOut:
    """Approve or reject a gate — sends the signal that resumes the workflow."""
    return await service.decide(gate_pk, decision)
