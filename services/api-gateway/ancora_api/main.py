"""FastAPI application factory.

Endpoints:
  GET  /healthz                        liveness (+ DB check)
  GET  /readyz                         readiness (503 unless DB reachable)
  GET  /v1/version                     build/version metadata
  GET  /v1/workflows                   list registered workflow definitions
  GET  /v1/workflows/{name}            get one definition
  POST /v1/workflows/{name}/runs       start a run
  GET  /v1/runs                        list runs
  GET  /v1/runs/{run_id}               get a run (refreshed from Temporal)
  POST /v1/runs/{run_id}/cancel        cancel a run
  GET  /v1/runs/{run_id}/cost          cost rollup for a run (Phase 3)
  GET  /v1/runs/{run_id}/retries       failed attempts + retry classification
  GET  /v1/workers                     list activity workers + live health
  GET  /v1/queues                      per-capability queue depth/worker counts
  GET  /v1/approvals                   human-approval inbox
  POST /v1/approvals/{id}/decision     approve/reject (signals the workflow)
  GET  /v1/plugins                     node-type catalog with JSON schemas
  GET  /v1/chaos                       killable targets + injection history
  POST /v1/chaos/inject                kill (SIGKILL) or restart a worker
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ancora_api import __version__
from ancora_api.approval_service import ApprovalNotFoundError
from ancora_api.idempotency import IdempotencyMiddleware
from ancora_api.routers import approvals, chaos, plugins, runs, workers, workflows
from ancora_api.service import NotFoundError
from ancora_api.settings import get_settings
from ancora_common import db
from ancora_common.logging import configure_logging
from ancora_common.temporal import connect

logger = logging.getLogger("ancora.api")


class VersionInfo(BaseModel):
    service: str
    version: str
    environment: str


class HealthStatus(BaseModel):
    status: str
    checks: dict[str, str]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger.info("api-gateway starting", extra={"environment": settings.environment})

    # Connect to Temporal in the background-friendly way: failure here degrades
    # the API to 503 on workflow endpoints rather than crashing the process.
    try:
        app.state.temporal_client = await connect(
            settings.temporal_address,
            settings.temporal_namespace,
            retries=5,
            backoff_seconds=1.0,
        )
        logger.info("connected to Temporal", extra={"address": settings.temporal_address})
    except Exception as exc:  # noqa: BLE001
        app.state.temporal_client = None
        logger.warning("Temporal not connected at startup: %s", exc)

    yield
    await db.dispose_engine()
    logger.info("api-gateway stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Ancora API",
        version=__version__,
        summary="Control plane for the Ancora durable AI workflow runtime.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Added after CORS so it sits *inside* it: a replayed response still gets the
    # CORS headers a browser needs, and preflights never reach the cache.
    app.add_middleware(IdempotencyMiddleware)

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ApprovalNotFoundError)
    async def _gate_not_found(request: Request, exc: ApprovalNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/healthz", response_model=HealthStatus, tags=["health"])
    async def healthz() -> HealthStatus:
        db_ok = await db.ping()
        return HealthStatus(status="ok", checks={"database": "ok" if db_ok else "unavailable"})

    @app.get("/readyz", response_model=HealthStatus, tags=["health"])
    async def readyz(response: Response) -> HealthStatus:
        db_ok = await db.ping()
        if not db_ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthStatus(
            status="ok" if db_ok else "degraded",
            checks={"database": "ok" if db_ok else "unavailable"},
        )

    @app.get("/v1/version", response_model=VersionInfo, tags=["meta"])
    async def version() -> VersionInfo:
        return VersionInfo(
            service="ancora-api",
            version=__version__,
            environment=settings.environment,
        )

    app.include_router(workflows.router)
    app.include_router(runs.router)
    app.include_router(workers.router)
    app.include_router(approvals.router)
    app.include_router(plugins.router)
    app.include_router(chaos.router)
    return app


app = create_app()
