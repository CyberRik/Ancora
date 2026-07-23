"""HTTP surface tests for the scheduler (AN-038, AN-047)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ancora_scheduler.api import create_app
from ancora_scheduler.config import ConfigStore, RateLimitRule, SchedulerConfig, Watermark
from ancora_scheduler.engine import AdmissionEngine


@pytest.fixture
def engine() -> AdmissionEngine:
    config = SchedulerConfig(
        rate_limits={"gemini": RateLimitRule(rps=1.0, burst=1.0)},
        watermarks={"default": Watermark(soft=100, hard=200)},
    )
    return AdmissionEngine(ConfigStore(path=None, config=config))


@pytest.fixture
def client(engine: AdmissionEngine) -> Iterator[TestClient]:
    with TestClient(create_app(engine)) as c:
        yield c


def _admit(client: TestClient, **over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "run_id": "wf-1",
        "node_id": "n1",
        "node_type": "llm",
        "task_queue": "ancora-cpu",
    }
    body.update(over)
    resp = client.post("/v1/admit", json=body)
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def test_admit_returns_a_decision(client: TestClient) -> None:
    body = _admit(client)
    assert body["outcome"] == "admit"
    assert body["queue_depth"] == 1


def test_admit_defers_and_reports_a_backoff(client: TestClient) -> None:
    _admit(client, node_id="a", provider="gemini")
    body = _admit(client, node_id="b", provider="gemini")
    assert body["outcome"] == "defer"
    assert body["rule"] == "rate_limit"
    assert float(body["retry_after"]) > 0  # type: ignore[arg-type]
    assert "rate limit" in str(body["reason"])


def test_complete_releases_the_slot(client: TestClient) -> None:
    _admit(client)
    resp = client.post("/v1/complete", json={"run_id": "wf-1", "node_id": "n1", "usd": 0.5})
    assert resp.status_code == 200
    assert resp.json()["released"] is True
    # A second report is harmless but honest about having found nothing.
    assert (
        client.post("/v1/complete", json={"run_id": "wf-1", "node_id": "n1"}).json()["released"]
        is False
    )


def test_state_endpoint_explains_why_work_is_stuck(client: TestClient) -> None:
    _admit(client, node_id="a", provider="gemini")
    _admit(client, node_id="b", provider="gemini")  # deferred
    state = client.get("/v1/scheduler/state").json()
    assert state["decisions"]["by_rule"]["rate_limit"] == 1
    assert any(q["queue"] == "ancora-cpu" for q in state["queues"])


def test_config_endpoint_reports_the_policy_in_force(client: TestClient) -> None:
    body = client.get("/v1/scheduler/config").json()
    assert body["config"]["rate_limits"]["gemini"]["rps"] == 1.0
    assert body["last_error"] is None


def test_metrics_are_prometheus_formatted(client: TestClient) -> None:
    _admit(client, node_id="a", provider="gemini")
    _admit(client, node_id="b", provider="gemini")
    text = client.get("/metrics").text
    assert "# TYPE ancora_scheduler_admissions_total counter" in text
    assert 'ancora_scheduler_admissions_total{outcome="admit"} 1.0' in text
    assert 'ancora_scheduler_rate_limit_deferrals_total{provider="gemini"} 1.0' in text
    # The autoscaling signal a scaler would actually key off.
    assert "ancora_scheduler_pending_demand" in text


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"
