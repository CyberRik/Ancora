"""API smoke tests (AN-008). These run without a live database."""

from __future__ import annotations

import httpx
import pytest

from ancora_api.main import create_app


@pytest.fixture()
def client() -> httpx.AsyncClient:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_healthz_is_live(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # DB may be up or down in CI; the check key must still be reported.
    assert "database" in body["checks"]


async def test_version_endpoint(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.get("/v1/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "ancora-api"
    assert body["version"].count(".") >= 2


async def test_workflow_endpoints_degrade_without_temporal(
    client: httpx.AsyncClient,
) -> None:
    # The ASGI transport doesn't run the lifespan, so no Temporal client is set:
    # workflow endpoints must degrade to 503, not crash.
    async with client:
        resp = await client.get("/v1/workflows")
    assert resp.status_code == 503


async def test_metrics_endpoint_exposes_prometheus_text(client: httpx.AsyncClient) -> None:
    from ancora_api.metrics import RUNS_STARTED

    RUNS_STARTED.inc(workflow="metrics_test")
    async with client:
        resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "# TYPE ancora_api_runs_started_total counter" in body
    assert 'ancora_api_runs_started_total{workflow="metrics_test"}' in body
