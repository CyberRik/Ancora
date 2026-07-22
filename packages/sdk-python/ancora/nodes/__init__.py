"""Ancora built-in node library (Phase 3).

Importing this package registers the built-in node types (LLM, HTTP, Approval;
Database/Python land alongside their runtime deps) in the node registry, so a
worker that imports ``ancora.nodes`` can execute any of them by ``type_name`` and
the API can list them at ``GET /v1/plugins``.
"""

from __future__ import annotations

from ancora.nodes.approval import ApprovalGate, ApprovalInput, ApprovalOutput
from ancora.nodes.base import (
    Cost,
    Logger,
    Node,
    NodeContext,
    NodeError,
    NodeSchema,
    ResourceHint,
    Sandbox,
)
from ancora.nodes.http import HTTPInput, HTTPNode, HTTPOutput, parse_retry_after, set_transport
from ancora.nodes.idempotency import canonical_json, derive_idempotency_key
from ancora.nodes.llm import (
    LLMInput,
    LLMMessage,
    LLMNode,
    LLMOutput,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    MockProvider,
    get_provider,
    register_provider,
)
from ancora.nodes.registry import catalog, get, register

__all__ = [
    # base
    "Node",
    "NodeContext",
    "NodeError",
    "NodeSchema",
    "Cost",
    "ResourceHint",
    "Sandbox",
    "Logger",
    # registry
    "register",
    "get",
    "catalog",
    # idempotency
    "derive_idempotency_key",
    "canonical_json",
    # llm
    "LLMNode",
    "LLMInput",
    "LLMOutput",
    "LLMMessage",
    "LLMRequest",
    "LLMResponse",
    "LLMProvider",
    "MockProvider",
    "register_provider",
    "get_provider",
    # http
    "HTTPNode",
    "HTTPInput",
    "HTTPOutput",
    "set_transport",
    "parse_retry_after",
    # approval
    "ApprovalGate",
    "ApprovalInput",
    "ApprovalOutput",
]
