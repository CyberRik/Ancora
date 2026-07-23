"""Run lifecycle endpoints (AN-018)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query, status

from ancora_api.chaos import ChaosService
from ancora_api.cost_service import CostService
from ancora_api.deps import (
    get_chaos_service,
    get_cost_service,
    get_service,
    get_worker_service,
)
from ancora_api.recovery import FleetLiveness
from ancora_api.schemas import (
    RetryAttemptOut,
    RunCostOut,
    RunGraphOut,
    RunLinks,
    RunLiveOut,
    RunOut,
    RunRecoveryOut,
    StartRunRequest,
    StartRunResponse,
)
from ancora_api.service import WorkflowService
from ancora_api.worker_service import WorkerService

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


@router.get("/runs/{run_id}/recovery", response_model=RunRecoveryOut)
async def get_run_recovery(
    run_id: uuid.UUID,
    service: WorkflowService = Depends(get_service),
    chaos: ChaosService = Depends(get_chaos_service),
    workers: WorkerService = Depends(get_worker_service),
) -> RunRecoveryOut:
    """What a worker death did to this run, and which clock it is waiting on.

    Reads the full history, so it is separate from ``/activities`` — the live
    poll stays cheap. Liveness and the injection log are both best-effort: the
    timeline is reconstructable from Temporal alone, they only sharpen it.
    """
    liveness: FleetLiveness | None = None
    try:
        liveness = FleetLiveness.from_workers(await workers.list_workers())
    except Exception:  # noqa: BLE001 — registry is an overlay, not a dependency
        liveness = None
    return await service.get_run_recovery(
        run_id, chaos_events=chaos.log.recent(), liveness=liveness
    )


@router.get("/runs/{run_id}/graph", response_model=RunGraphOut)
async def get_run_graph(
    run_id: uuid.UUID,
    service: WorkflowService = Depends(get_service),
) -> RunGraphOut:
    """The DAG this run executed, with per-vertex live state.

    Reconstructed from history rather than declared, because a workflow's graph
    is emergent: the fan-out width comes from the input and the tail depends on
    which branch the run took. A graph that expired at its gate is a different
    graph from one that was approved, and both are true.
    """
    return await service.get_run_graph(run_id)


@router.get("/runs/{run_id}/cost", response_model=RunCostOut)
async def get_run_cost(
    run_id: uuid.UUID,
    service: WorkflowService = Depends(get_service),
    costs: CostService = Depends(get_cost_service),
) -> RunCostOut:
    """What this run cost, with by-node / by-model / by-provider rollups (AN-057)."""
    run = await service.get_run(run_id)
    return await costs.run_cost(run_id, run.temporal_wf_id)


@router.get("/runs/{run_id}/retries", response_model=list[RetryAttemptOut])
async def get_run_retries(
    run_id: uuid.UUID,
    service: WorkflowService = Depends(get_service),
    costs: CostService = Depends(get_cost_service),
) -> list[RetryAttemptOut]:
    """Every failed attempt in this run and whether it was judged retryable."""
    run = await service.get_run(run_id)
    return await costs.run_retries(run.temporal_wf_id)


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
