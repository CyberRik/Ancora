"""FastAPI application factory and Phase 0 endpoints.

Endpoints:
  GET /healthz      liveness (200 while the process is up; reports DB check)
  GET /readyz       readiness (503 unless the database is reachable)
  GET /v1/version   build/version metadata
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ancora_api import __version__, db
from ancora_api.logging import configure_logging
from ancora_api.settings import get_settings

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

    @app.get("/healthz", response_model=HealthStatus, tags=["health"])
    async def healthz() -> HealthStatus:
        """Liveness: 200 while the app is up. Includes a best-effort DB check."""
        db_ok = await db.ping()
        return HealthStatus(
            status="ok",
            checks={"database": "ok" if db_ok else "unavailable"},
        )

    @app.get("/readyz", response_model=HealthStatus, tags=["health"])
    async def readyz(response: Response) -> HealthStatus:
        """Readiness: 503 unless the database is reachable."""
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

    return app


app = create_app()
