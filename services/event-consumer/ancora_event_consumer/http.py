"""A minimal health/metrics HTTP surface for the consumer (Phase 4c).

The consumer is otherwise a headless loop. It exposes just enough HTTP for an
operator and Prometheus: ``/healthz`` for a container healthcheck and ``/metrics``
for scraping. Runs as a uvicorn server task alongside the projector and reconciler
in the same event loop.
"""

from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from ancora_common.metrics import CONTENT_TYPE
from ancora_event_consumer.metrics import REGISTRY


def create_app() -> FastAPI:
    app = FastAPI(title="Ancora Event Consumer", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(REGISTRY.render(), media_type=CONTENT_TYPE)

    return app


async def serve(port: int, stop: asyncio.Event) -> None:
    """Serve health/metrics until ``stop`` is set."""
    config = uvicorn.Config(create_app(), host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)

    async def _watch() -> None:
        await stop.wait()
        server.should_exit = True

    watcher = asyncio.create_task(_watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()
