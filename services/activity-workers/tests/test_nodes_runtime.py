"""Unit tests for the ``run_node`` activity (Phase 3, E4/E5).

Uses Temporal's ``ActivityEnvironment`` so the activity runs with a real activity
context (``activity.info()``) but no server. Proves node execution + cost, and the
exactly-once side-effect guarantee via the inbox guard.
"""

from __future__ import annotations

from typing import Any

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ancora.nodes import MockProvider, register_provider, set_transport
from ancora.nodes.http import HTTPResponse
from ancora.nodes.llm import clear_providers
from ancora_activity_worker import runtime
from ancora_activity_worker.nodes_runtime import run_node
from ancora_common.inbox import InMemoryInboxGuard


@pytest.fixture(autouse=True)
def _clean() -> Any:
    runtime.reset()
    clear_providers()
    register_provider(MockProvider("mock"))
    register_provider(MockProvider("mock-secondary"))
    yield
    runtime.reset()
    clear_providers()


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
