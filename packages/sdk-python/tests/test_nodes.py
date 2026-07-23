"""Unit tests for the built-in node library (Phase 3, E4/E5)."""

from __future__ import annotations

from typing import Any

import pytest

from ancora.nodes import (
    ApprovalGate,
    ApprovalInput,
    Cost,
    HTTPInput,
    HTTPNode,
    HTTPOutput,
    LLMInput,
    LLMMessage,
    LLMNode,
    MockProvider,
    NodeContext,
    NodeError,
    catalog,
    derive_idempotency_key,
    get_provider,
    register_provider,
    set_transport,
)
from ancora.nodes.http import HTTPResponse, parse_retry_after
from ancora.nodes.llm import clear_providers


def ctx(node_id: str = "n1", key: str = "k1") -> NodeContext:
    return NodeContext(node_id=node_id, idempotency_key=key)


# --------------------------------------------------------------------------- #
# Idempotency-key derivation (AN-062)
# --------------------------------------------------------------------------- #
def test_idempotency_key_is_stable_across_dict_ordering() -> None:
    a = derive_idempotency_key(workflow_id="wf", node_id="n", payload={"x": 1, "y": 2})
    b = derive_idempotency_key(workflow_id="wf", node_id="n", payload={"y": 2, "x": 1})
    assert a == b
    assert a.startswith("n-")


def test_idempotency_key_varies_by_input_and_node() -> None:
    base = derive_idempotency_key(workflow_id="wf", node_id="n", payload={"x": 1})
    assert base != derive_idempotency_key(workflow_id="wf", node_id="n", payload={"x": 2})
    assert base != derive_idempotency_key(workflow_id="wf", node_id="other", payload={"x": 1})


def test_idempotency_key_override_is_verbatim() -> None:
    assert (
        derive_idempotency_key(workflow_id="wf", node_id="n", payload={}, override="req-42")
        == "req-42"
    )


# --------------------------------------------------------------------------- #
# Cost accumulation (AN-056/057)
# --------------------------------------------------------------------------- #
def test_cost_addition_merges_and_collapses_provenance() -> None:
    total = Cost(usd=0.1, input_tokens=10, provider="mock", model="m") + Cost(
        usd=0.2, output_tokens=5, provider="mock", model="m"
    )
    assert total.usd == pytest.approx(0.3)
    assert total.input_tokens == 10 and total.output_tokens == 5
    assert total.provider == "mock"
    mixed = Cost(provider="a") + Cost(provider="b")
    assert mixed.provider is None


def test_an_empty_accumulator_does_not_erase_provenance() -> None:
    # NodeContext starts at Cost(); if the empty side counted as a conflicting
    # value, every node's first recorded cost would land in the ledger with a
    # null provider and model — and the by-model rollup would be all "—".
    ctx = NodeContext(node_id="n", idempotency_key="k")
    ctx.record_cost(Cost(usd=0.5, provider="gemini", model="gemini-3.5-flash-lite"))
    assert ctx.total_cost.provider == "gemini"
    assert ctx.total_cost.model == "gemini-3.5-flash-lite"


def test_provenance_survives_repeated_costs_from_one_source() -> None:
    ctx = NodeContext(node_id="n", idempotency_key="k")
    ctx.record_cost(Cost(usd=0.1, provider="gemini", model="flash"))
    ctx.record_cost(Cost(usd=0.2, provider="gemini", model="flash"))
    assert ctx.total_cost.usd == pytest.approx(0.3)
    assert ctx.total_cost.provider == "gemini"


# --------------------------------------------------------------------------- #
# Registry / catalog (AN-058)
# --------------------------------------------------------------------------- #
def test_catalog_lists_builtins_with_schemas() -> None:
    types = {n.type_name for n in catalog()}
    assert {"llm", "http", "approval"} <= types
    llm = next(n for n in catalog() if n.type_name == "llm")
    assert "properties" in llm.input_schema
    assert llm.idempotent is True


# --------------------------------------------------------------------------- #
# LLMNode (AN-051)
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clean_providers() -> Any:
    clear_providers()
    yield
    clear_providers()


async def test_llm_runs_against_mock_and_records_cost() -> None:
    register_provider(MockProvider("mock", price_per_1k=0.002))
    node = LLMNode()
    c = ctx()
    out = await node.execute(
        LLMInput(messages=[LLMMessage(role="user", content="hello world")], model="m1"), c
    )
    assert out.provider == "mock"
    assert "hello world" in out.text
    assert out.usd > 0
    assert c.total_cost.usd == pytest.approx(out.usd)
    assert c.total_cost.input_tokens == out.input_tokens


async def test_llm_falls_back_to_secondary_on_transient_failure() -> None:
    register_provider(MockProvider("primary", fail_times=1, transient=True))
    register_provider(MockProvider("secondary"))
    out = await LLMNode().execute(
        LLMInput(
            messages=[LLMMessage(role="user", content="hi")],
            model="m",
            providers=["primary", "secondary"],
        ),
        ctx(),
    )
    assert out.provider == "secondary"
    assert out.fell_back_from == ["primary"]


async def test_llm_does_not_fall_back_on_terminal_error() -> None:
    register_provider(MockProvider("primary", fail_times=1, transient=False))
    register_provider(MockProvider("secondary"))
    with pytest.raises(NodeError) as ei:
        await LLMNode().execute(
            LLMInput(
                messages=[LLMMessage(role="user", content="hi")],
                providers=["primary", "secondary"],
            ),
            ctx(),
        )
    assert ei.value.transient is False


async def test_llm_all_providers_failed_is_transient() -> None:
    register_provider(MockProvider("p1", fail_times=1))
    with pytest.raises(NodeError) as ei:
        await LLMNode().execute(
            LLMInput(messages=[LLMMessage(role="user", content="x")], providers=["p1"]), ctx()
        )
    assert ei.value.transient is True


def test_get_provider_missing_is_terminal() -> None:
    with pytest.raises(NodeError) as ei:
        get_provider("nope")
    assert ei.value.transient is False


# --------------------------------------------------------------------------- #
# HTTPNode (AN-052)
# --------------------------------------------------------------------------- #
class FakeTransport:
    def __init__(self, response: HTTPResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str],
        json: Any | None,
        timeout: float,
    ) -> HTTPResponse:
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        return self.response


async def test_http_success_returns_parsed_json() -> None:
    set_transport(FakeTransport(HTTPResponse(status=200, text='{"ok": true}')))
    out = await HTTPNode().execute(HTTPInput(method="GET", url="https://x/api"), ctx())
    assert isinstance(out, HTTPOutput)
    assert out.status == 200
    assert out.json_body == {"ok": True}


async def test_http_templates_url_and_headers() -> None:
    ft = FakeTransport(HTTPResponse(status=200, text=""))
    set_transport(ft)
    await HTTPNode().execute(
        HTTPInput(
            method="GET",
            url="https://x/{id}",
            headers={"Authorization": "Bearer {tok}"},
            template_vars={"id": "42", "tok": "secret"},
        ),
        ctx(),
    )
    assert ft.calls[0]["url"] == "https://x/42"
    assert ft.calls[0]["headers"]["Authorization"] == "Bearer secret"


async def test_http_429_is_transient_with_retry_after() -> None:
    ft = FakeTransport(HTTPResponse(status=429, headers={"retry-after": "2.5"}))
    set_transport(ft)
    with pytest.raises(NodeError) as ei:
        await HTTPNode().execute(HTTPInput(url="https://x"), ctx())
    assert ei.value.transient is True
    assert ei.value.retry_after == pytest.approx(2.5)
    assert ft.calls  # request was actually attempted


async def test_http_404_is_terminal() -> None:
    set_transport(FakeTransport(HTTPResponse(status=404)))
    with pytest.raises(NodeError) as ei:
        await HTTPNode().execute(HTTPInput(url="https://x"), ctx())
    assert ei.value.transient is False


async def test_http_500_is_transient() -> None:
    set_transport(FakeTransport(HTTPResponse(status=500)))
    with pytest.raises(NodeError) as ei:
        await HTTPNode().execute(HTTPInput(url="https://x"), ctx())
    assert ei.value.transient is True


def test_parse_retry_after() -> None:
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("Wed, 21 Oct 2025 07:28:00 GMT") is None


# --------------------------------------------------------------------------- #
# ApprovalGate (AN-055) — must not run as an activity
# --------------------------------------------------------------------------- #
async def test_approval_gate_refuses_activity_execution() -> None:
    with pytest.raises(NodeError):
        await ApprovalGate().execute(ApprovalInput(gate_id="g1"), ctx())
