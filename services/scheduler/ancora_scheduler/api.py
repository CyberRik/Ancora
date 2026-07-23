"""The scheduler's HTTP surface (AN-038).

``POST /v1/admit`` is the hot path: the activity worker calls it immediately
before executing a node and honours the verdict. Everything else is
introspection — the endpoints an operator hits when work is not moving and they
need to know which governor is holding it.

The API is deliberately synchronous and stateless-per-request. Admission is an
in-memory decision measured in microseconds; making it an RPC buys operational
visibility (one place to inspect and change policy) without buying latency.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ancora_scheduler import __version__
from ancora_scheduler.config import ConfigStore
from ancora_scheduler.engine import AdmissionEngine, AdmissionRequest
from ancora_scheduler.metrics import render
from ancora_scheduler.settings import get_settings

logger = logging.getLogger("ancora.scheduler.api")


# --------------------------------------------------------------------------- #
# Wire schemas
# --------------------------------------------------------------------------- #
class AdmitRequest(BaseModel):
    """What a worker tells the scheduler before running one node."""

    run_id: str
    node_id: str
    node_type: str = "unknown"
    task_queue: str
    tenant: str = "default"
    # 1 = high, 3 = normal, 5 = bulk (see ancora.policy).
    priority: int = 3
    attempt: int = 1
    provider: str | None = None
    model: str | None = None
    tokens: float = Field(default=1.0, gt=0)
    estimated_usd: float = Field(default=0.0, ge=0)
    deadline_seconds: float | None = None


class AdmitResponse(BaseModel):
    outcome: str  # admit | defer | reject
    rule: str
    retry_after: float = 0.0
    reason: str = ""
    warning: str | None = None
    timeout_seconds: float | None = None
    queue_depth: int = 0


class CompleteRequest(BaseModel):
    """Reported by the worker once a node finishes, win or lose.

    Releases the in-flight slot that backpressure counts and adds the node's
    actual cost to the budget ledger.
    """

    run_id: str
    node_id: str
    tenant: str = "default"
    usd: float = Field(default=0.0, ge=0)


class CompleteResponse(BaseModel):
    # False when the slot had already expired by TTL — harmless, but worth seeing.
    released: bool


def build_router(engine: AdmissionEngine) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["scheduler"])

    @router.post("/admit", response_model=AdmitResponse)
    async def admit(req: AdmitRequest) -> AdmitResponse:
        decision = engine.admit(
            AdmissionRequest(
                run_id=req.run_id,
                node_id=req.node_id,
                node_type=req.node_type,
                task_queue=req.task_queue,
                tenant=req.tenant,
                priority=req.priority,
                attempt=req.attempt,
                provider=req.provider,
                model=req.model,
                tokens=req.tokens,
                estimated_usd=req.estimated_usd,
                deadline_seconds=req.deadline_seconds,
            )
        )
        if decision.outcome != "admit":
            logger.info("%s %s/%s: %s", decision.outcome, req.run_id, req.node_id, decision.reason)
        return AdmitResponse(
            outcome=decision.outcome,
            rule=decision.rule,
            retry_after=decision.retry_after,
            reason=decision.reason,
            warning=decision.warning,
            timeout_seconds=decision.timeout_seconds,
            queue_depth=decision.queue_depth,
        )

    @router.post("/complete", response_model=CompleteResponse)
    async def complete(req: CompleteRequest) -> CompleteResponse:
        released = engine.complete(
            run_id=req.run_id, node_id=req.node_id, tenant=req.tenant, usd=req.usd
        )
        return CompleteResponse(released=released)

    @router.get("/scheduler/config")
    async def read_config() -> dict[str, Any]:
        """The policy actually in force, plus any rejected reload (AN-048)."""
        engine.store.reload_if_changed()
        return {
            "path": str(engine.store.path) if engine.store.path else None,
            "config": engine.store.config.model_dump(mode="json"),
            "last_error": engine.store.last_error,
        }

    @router.get("/scheduler/state")
    async def read_state() -> dict[str, Any]:
        """Live governor state — the "why is my work stuck" endpoint."""
        depths = engine.inflight.depths()
        return {
            "queues": [
                {
                    "queue": q,
                    "depth": depth,
                    "watermark": engine.store.config.watermark_for(q).model_dump(mode="json"),
                    "lanes": engine.lanes.by_queue(q),
                    "fair_share": engine.fair.snapshot(q),
                }
                for q, depth in sorted(depths.items())
            ],
            "counters": engine.inflight.counters(),
            "decisions": {
                "by_outcome": engine.stats.by_outcome,
                "by_rule": engine.stats.by_rule,
            },
            "budget": engine.ledger.snapshot(),
        }

    return router


def create_app(engine: AdmissionEngine | None = None) -> FastAPI:
    settings = get_settings()
    active = engine or AdmissionEngine(ConfigStore.from_path(settings.scheduler_config_path))

    app = FastAPI(
        title="Ancora Scheduler",
        version=__version__,
        summary="Admission control: rate limits, backpressure, fairness, budgets, deadlines.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.engine = active

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", tags=["meta"], response_model=None)
    async def metrics() -> Any:
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(render(active), media_type="text/plain; version=0.0.4")

    @app.get("/v1/version", tags=["meta"])
    async def version() -> dict[str, str]:
        return {
            "service": "ancora-scheduler",
            "version": __version__,
            "environment": settings.environment,
        }

    app.include_router(build_router(active))
    return app
