"""Tests for the consumer's health/metrics HTTP surface (Phase 4c)."""

from __future__ import annotations

from ancora_event_consumer.http import create_app
from ancora_event_consumer.metrics import EVENTS_PROJECTED
from starlette.testclient import TestClient


def test_healthz_ok() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_metrics_renders_counter() -> None:
    EVENTS_PROJECTED.inc(kind="activity.started")
    with TestClient(create_app()) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "# TYPE ancora_consumer_events_projected_total counter" in body
    assert 'ancora_consumer_events_projected_total{kind="activity.started"}' in body
