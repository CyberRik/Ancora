"""Chaos Lab endpoints — kill a worker and watch the run survive it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ancora_api.chaos import ChaosDisabledError, ChaosService, ChaosTarget, ChaosTargetError
from ancora_api.deps import get_chaos_service, get_service
from ancora_api.service import WorkflowService
from ancora_api.schemas import (
    ChaosEventOut,
    ChaosInjectRequest,
    ChaosStatusOut,
    ChaosTargetOut,
)

router = APIRouter(prefix="/v1", tags=["chaos"])


def _target_out(t: ChaosTarget) -> ChaosTargetOut:
    return ChaosTargetOut(service=t.service, name=t.name, state=t.state, killable=t.killable)


@router.get("/chaos", response_model=ChaosStatusOut)
async def chaos_status(service: ChaosService = Depends(get_chaos_service)) -> ChaosStatusOut:
    """What can be killed right now, plus the recent injection history.

    Returns ``enabled: false`` with a reason rather than an error, so the UI can
    explain *why* the buttons are missing instead of just failing to load.
    """
    if not service.enabled:
        return ChaosStatusOut(
            enabled=False,
            project=service.project,
            reason=(
                "Chaos injection is disabled. It needs the Docker socket, which lets the "
                "API control its host's containers — so it is opt-in. The local compose "
                "stack enables it; run `make up` to try it."
            ),
        )
    try:
        targets = await service.list_targets()
    except Exception as exc:  # noqa: BLE001 — surface the cause, never 500 the page
        return ChaosStatusOut(
            enabled=False,
            project=service.project,
            reason=f"Docker socket unreachable at {service.socket_path}: {exc}",
            events=[ChaosEventOut(**e) for e in service.log.recent()],
        )
    return ChaosStatusOut(
        enabled=True,
        project=service.project,
        targets=[_target_out(t) for t in targets],
        events=[ChaosEventOut(**e) for e in service.log.recent()],
    )


@router.post("/chaos/inject", response_model=ChaosTargetOut)
async def inject(
    req: ChaosInjectRequest,
    service: ChaosService = Depends(get_chaos_service),
    workflow_service: WorkflowService = Depends(get_service),
) -> ChaosTargetOut:
    """Kill (or restart) a worker container. The kill is a real SIGKILL."""
    if req.action == "kill":
        runs = await workflow_service.list_runs(limit=50)
        if not any(r.status == "Running" for r in runs):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot kill worker: no run is currently in progress",
            )
    
    try:
        if req.action == "kill":
            target = await service.kill(req.service, signal=req.signal)
        elif req.action == "restart":
            target = await service.restart(req.service)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown chaos action '{req.action}' (expected 'kill' or 'restart')",
            )
    except ChaosDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ChaosTargetError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _target_out(target)
