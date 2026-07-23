"""Unit tests for the ``run_node`` activity (Phase 3, E4/E5).

Uses Temporal's ``ActivityEnvironment`` so the activity runs with a real activity
context (``activity.info()``) but no server. Proves node execution + cost, and the
exactly-once side-effect guarantee via the inbox guard.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ancora.nodes import MockProvider, register_provider, set_transport
from ancora.nodes.http import HTTPResponse
from ancora.nodes.llm import clear_providers
from ancora_activity_worker import runtime
from ancora_activity_worker.nodes_runtime import _provider_of, run_node
from ancora_common import projections
from ancora_common.inbox import InMemoryInboxGuard
from ancora_common.scheduler_client import Verdict


@pytest.fixture(autouse=True)
def _clean() -> Any:
    runtime.reset()
    clear_providers()
    register_provider(MockProvider("mock"))
    register_provider(MockProvider("mock-secondary"))
    # No database in unit tests; the ledger/retry projections are reporting only.
    projections.set_enabled(False)
    yield
    runtime.reset()
    clear_providers()
    projections.set_enabled(True)


class CountingTransport:
    def __init__(self, response: HTTPResponse) -> None:
        self.response = response
        self.calls = 0

    async def request(self, method: str, url: str, **kwargs: Any) -> HTTPResponse:
        self.calls += 1
        return self.response


def _llm_req(node_id: str, key: str, content: str = "hello") -> dict[str, Any]:
    return {
        "type_name": "llm",
        "node_id": node_id,
        "input": {"messages": [{"role": "user", "content": content}], "providers": ["mock"]},
        "idempotency_key": key,
        "workflow_id": "wf-1",
    }


async def test_run_node_executes_llm_and_returns_cost() -> None:
    env = ActivityEnvironment()
    result = await env.run(run_node, _llm_req("search", "k-search"))
    assert result["output"]["provider"] == "mock"
    assert "hello" in result["output"]["text"]
    assert result["cost"]["usd"] > 0


async def test_run_node_http_fires_exactly_once_via_inbox() -> None:
    inbox = InMemoryInboxGuard()
    runtime.set_inbox(inbox)
    transport = CountingTransport(HTTPResponse(status=201, text='{"id": 7}'))
    set_transport(transport)

    req = {
        "type_name": "http",
        "node_id": "publish",
        "input": {"method": "POST", "url": "https://api/publish", "json_body": {"x": 1}},
        "idempotency_key": "publish-key-1",
        "workflow_id": "wf-1",
    }
    env = ActivityEnvironment()
    first = await env.run(run_node, req)
    second = await env.run(run_node, req)  # a retry/replay with the same key

    assert first == second
    assert first["output"]["status"] == 201
    assert transport.calls == 1  # the effect fired exactly once
    assert inbox.effect_runs == 1


async def test_run_node_terminal_error_is_non_retryable() -> None:
    # A 404 is a terminal HTTP error → must surface as non_retryable.
    runtime.set_inbox(InMemoryInboxGuard())
    set_transport(CountingTransport(HTTPResponse(status=404)))
    req = {
        "type_name": "http",
        "node_id": "publish",
        "input": {"method": "POST", "url": "https://api/x"},
        "idempotency_key": "k-404",
        "workflow_id": "wf-1",
    }
    env = ActivityEnvironment()
    with pytest.raises(ApplicationError) as ei:
        await env.run(run_node, req)
    assert ei.value.non_retryable is True


async def test_run_node_transient_error_is_retryable() -> None:
    runtime.set_inbox(InMemoryInboxGuard())
    set_transport(CountingTransport(HTTPResponse(status=503, headers={"retry-after": "1"})))
    req = {
        "type_name": "http",
        "node_id": "publish",
        "input": {"method": "GET", "url": "https://api/x"},
        "idempotency_key": "k-503",
        "workflow_id": "wf-1",
    }
    env = ActivityEnvironment()
    with pytest.raises(ApplicationError) as ei:
        await env.run(run_node, req)
    assert ei.value.non_retryable is False


# --------------------------------------------------------------------------- #
# Admission control on the execution path (AN-038, AN-040)
# --------------------------------------------------------------------------- #
class FakeScheduler:
    """Stands in for the scheduler HTTP client with a scripted verdict."""

    def __init__(self, verdict: Verdict) -> None:
        self.enabled = True
        self.verdict = verdict
        self.admit_calls: list[dict[str, Any]] = []
        self.completions: list[dict[str, Any]] = []

    async def admit(self, payload: dict[str, Any]) -> Verdict:
        self.admit_calls.append(payload)
        return self.verdict

    async def complete(self, payload: dict[str, Any]) -> None:
        self.completions.append(payload)


async def test_a_deferred_node_becomes_a_retryable_error_with_a_backoff() -> None:
    scheduler = FakeScheduler(
        Verdict(outcome="defer", rule="rate_limit", retry_after=2.5, reason="gemini at limit")
    )
    runtime.set_scheduler(scheduler)

    env = ActivityEnvironment()
    with pytest.raises(ApplicationError) as ei:
        await env.run(run_node, _llm_req("search", "k-defer"))

    # A deferral is durable waiting, not failure: Temporal must re-deliver it,
    # and it must wait exactly as long as the scheduler asked.
    assert ei.value.non_retryable is False
    assert ei.value.type == "SchedulerDeferred"
    assert ei.value.next_retry_delay == timedelta(seconds=2.5)


async def test_a_deferred_node_never_starts_executing() -> None:
    scheduler = FakeScheduler(Verdict(outcome="defer", rule="backpressure", retry_after=1.0))
    runtime.set_scheduler(scheduler)
    transport = CountingTransport(HTTPResponse(status=200, text="{}"))
    set_transport(transport)
    runtime.set_inbox(InMemoryInboxGuard())

    env = ActivityEnvironment()
    with pytest.raises(ApplicationError):
        await env.run(
            run_node,
            {
                "type_name": "http",
                "node_id": "publish",
                "input": {"method": "POST", "url": "https://api/x"},
                "idempotency_key": "k-deferred",
                "workflow_id": "wf-1",
            },
        )
    # Admission runs before the effect, so a deferred node leaves no trace.
    assert transport.calls == 0


async def test_a_rejected_node_fails_fast() -> None:
    scheduler = FakeScheduler(
        Verdict(outcome="reject", rule="budget", reason="run budget exceeded")
    )
    runtime.set_scheduler(scheduler)

    env = ActivityEnvironment()
    with pytest.raises(ApplicationError) as ei:
        await env.run(run_node, _llm_req("search", "k-reject"))

    # Waiting cannot make money or time reappear — retrying is pure waste.
    assert ei.value.non_retryable is True
    assert ei.value.type == "SchedulerRejected"


async def test_an_admitted_node_reports_its_cost_back_to_the_scheduler() -> None:
    scheduler = FakeScheduler(Verdict(outcome="admit"))
    runtime.set_scheduler(scheduler)

    env = ActivityEnvironment()
    result = await env.run(run_node, _llm_req("search", "k-admit"))

    assert scheduler.completions[0]["usd"] == pytest.approx(result["cost"]["usd"])
    assert scheduler.completions[0]["node_id"] == "search"


async def test_a_failed_node_still_releases_its_inflight_slot() -> None:
    scheduler = FakeScheduler(Verdict(outcome="admit"))
    runtime.set_scheduler(scheduler)
    runtime.set_inbox(InMemoryInboxGuard())
    set_transport(CountingTransport(HTTPResponse(status=404)))

    env = ActivityEnvironment()
    with pytest.raises(ApplicationError):
        await env.run(
            run_node,
            {
                "type_name": "http",
                "node_id": "publish",
                "input": {"method": "POST", "url": "https://api/x"},
                "idempotency_key": "k-fail-release",
                "workflow_id": "wf-1",
            },
        )
    # Otherwise a run of failing nodes would slowly wedge the queue's watermark.
    assert scheduler.completions and scheduler.completions[0]["node_id"] == "publish"


async def test_scheduling_context_is_forwarded_from_the_workflow() -> None:
    scheduler = FakeScheduler(Verdict(outcome="admit"))
    runtime.set_scheduler(scheduler)

    req = _llm_req("search", "k-ctx")
    req["scheduling"] = {
        "tenant": "acme",
        "priority": 1,
        "task_queue": "ancora-gpu",
        "deadline_seconds": 30.0,
    }
    env = ActivityEnvironment()
    await env.run(run_node, req)

    sent = scheduler.admit_calls[0]
    assert sent["tenant"] == "acme"
    assert sent["priority"] == 1
    assert sent["task_queue"] == "ancora-gpu"
    assert sent["deadline_seconds"] == 30.0


def test_llm_calls_bucket_by_their_first_provider() -> None:
    # The chain's first entry is the one whose quota is actually at risk.
    provider, model = _provider_of(
        "llm", {"providers": ["gemini", "openai"], "model": "gemini-3.5-flash-lite"}
    )
    assert provider == "gemini"
    assert model == "gemini-3.5-flash-lite"


def test_http_calls_bucket_by_host() -> None:
    # So a flaky API cannot be starved by unrelated traffic to another host.
    provider, model = _provider_of("http", {"url": "https://api.example.com/v1/publish"})
    assert provider == "api.example.com"
    assert model is None


def test_nodes_without_a_provider_are_unbucketed() -> None:
    assert _provider_of("database", {"sql": "SELECT 1"}) == (None, None)
