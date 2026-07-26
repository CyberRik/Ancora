"""Chaos Lab endpoints — kill a worker and watch the run survive it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ancora_api.chaos import ChaosDisabledError, ChaosService, ChaosTarget, ChaosTargetError
from ancora_api.chaos_experiments import SCENARIOS, ChaosExperimentRunner, ExperimentLog
from ancora_api.deps import get_chaos_service, get_experiment_log, get_service
from ancora_api.schemas import (
    ChaosEventOut,
    ChaosExperimentResultOut,
    ChaosExperimentsOut,
    ChaosInjectRequest,
    ChaosStatusOut,
    ChaosTargetOut,
)
from ancora_api.service import WorkflowService

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


@router.get("/chaos/experiments", response_model=ChaosExperimentsOut)
async def list_experiments(
    service: ChaosService = Depends(get_chaos_service),
    log: ExperimentLog = Depends(get_experiment_log),
) -> ChaosExperimentsOut:
    """The scenario library and recent verdicts — chaos as an *asserting* feature."""
    if not service.enabled:
        return ChaosExperimentsOut(
            enabled=False,
            scenarios=[s.to_out() for s in SCENARIOS.values()],
            reason="Chaos injection is disabled; experiments need the Docker socket.",
        )
    return ChaosExperimentsOut(
        enabled=True,
        scenarios=[s.to_out() for s in SCENARIOS.values()],
        recent=log.recent(),
    )


@router.post("/chaos/experiments/{name}", response_model=ChaosExperimentResultOut)
async def run_experiment(
    name: str,
    chaos: ChaosService = Depends(get_chaos_service),
    workflow_service: WorkflowService = Depends(get_service),
    log: ExperimentLog = Depends(get_experiment_log),
) -> ChaosExperimentResultOut:
    """Run a scenario end-to-end: start a run, inject the fault mid-flight, assert.

    Long-running by nature — it waits out the real recovery — but bounded so a
    wedged run cannot hang the request.
    """
    if name not in SCENARIOS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown scenario '{name}'; known: {', '.join(sorted(SCENARIOS))}",
        )
    try:
        chaos._require_enabled()
    except ChaosDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    result = await ChaosExperimentRunner(workflow_service, chaos).run(name)
    log.record(result)
    return result
