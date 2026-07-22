"""The ``run_node`` activity — the single execution surface for built-in nodes.

Every built-in node (LLM, HTTP, Database, Python) runs through this one activity:
it resolves the node type from the registry, validates the input against the
node's schema, builds a :class:`NodeContext`, and executes it. Side-effecting
nodes (``idempotent = False``, e.g. HTTP POST) are wrapped in the inbox guard so a
retry or replay returns the stored result instead of re-issuing the effect
(AN-061). Node failures are mapped to Temporal ``ApplicationError`` with
``non_retryable`` set from the node's transient/terminal classification (AN-044),
so transient errors retry with backoff and terminal ones fail fast.

Importing this module registers the built-in nodes and the CI mock LLM provider.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

import ancora.nodes  # noqa: F401 — import registers the built-in node types
from ancora.nodes.base import NodeContext, NodeError
from ancora.nodes.llm import MockProvider, register_provider
from ancora.nodes.registry import get as get_node
from ancora_activity_worker.runtime import get_inbox, get_node_recorder

logger = logging.getLogger("ancora.runtime.nodes")

# CI/dev default so LLM nodes work without API keys. Real providers are registered
# at worker startup from configuration.
register_provider(MockProvider("mock"))
register_provider(MockProvider("mock-secondary"))


class NodeRequest(dict[str, Any]):
    """Wire shape passed by ``Workflow.call_node``; kept as a plain dict for Temporal."""


async def _execute_node(
    type_name: str, node_id: str, payload: dict[str, Any], key: str
) -> dict[str, Any]:
    node_cls = get_node(type_name)
    node = node_cls()
    parsed = node_cls.input_model.model_validate(payload)
    ctx = NodeContext(
        node_id=node_id,
        idempotency_key=key,
        attempt=activity.info().attempt,
        log=activity.logger,
    )
    output = await node.execute(parsed, ctx)
    return {
        "output": output.model_dump(mode="json"),
        "cost": ctx.total_cost.model_dump(mode="json"),
    }


@activity.defn(name="run_node")
async def run_node(req: dict[str, Any]) -> dict[str, Any]:
    """Resolve, validate, and execute a built-in node with exactly-once effects."""
    type_name = str(req["type_name"])
    node_id = str(req["node_id"])
    payload: dict[str, Any] = dict(req.get("input") or {})
    key = str(req["idempotency_key"])
    wf_id = req.get("workflow_id")

    node_cls = get_node(type_name)
    recorder = get_node_recorder()
    await recorder.record_start(
        {
            "temporal_wf_id": wf_id,
            "node_name": type_name,
            "capability": "io",
            "backend": "node",
            "ray_task_id": None,
            "attempt": activity.info().attempt,
            "status": "Running",
        }
    )

    async def effect() -> dict[str, Any]:
        return await _execute_node(type_name, node_id, payload, key)

    try:
        if node_cls.idempotent:
            result = await effect()
        else:
            # Guard side-effecting nodes so a retried effect fires exactly once.
            result = await get_inbox().get_or_run(
                key, effect, temporal_wf_id=wf_id, node_id=node_id
            )
    except NodeError as exc:
        await recorder.record_finish(
            {"temporal_wf_id": wf_id, "node_name": type_name, "status": "Failed"}
        )
        # Terminal node errors must not retry; transient ones should.
        raise ApplicationError(str(exc), type="NodeError", non_retryable=not exc.transient) from exc

    await recorder.record_finish(
        {"temporal_wf_id": wf_id, "node_name": type_name, "status": "Completed"}
    )
    return result
