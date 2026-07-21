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
