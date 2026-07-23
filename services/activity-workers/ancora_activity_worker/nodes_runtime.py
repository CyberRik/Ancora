"""The ``run_node`` activity — the single execution surface for built-in nodes.

Every built-in node (LLM, HTTP, Database, Python, Approval) runs through this one
activity. In order, it:

1. **Asks the scheduler whether the node may start** (AN-038). A ``defer`` becomes
   a retryable ``ApplicationError`` carrying ``next_retry_delay`` — the work goes
   back into Temporal's durable history and is re-delivered after the backoff,
   which is why admission control costs nothing when the scheduler restarts. A
   ``reject`` (deadline blown, hard budget) becomes a *non-retryable* failure,
   because waiting cannot fix either.
2. Resolves the node type from the registry and validates the input against its
   schema, so a node never sees a malformed payload.
3. Executes it — wrapping side-effecting nodes (``idempotent = False``) in the
   inbox guard, so a retry or replay returns the stored result rather than
   re-issuing the effect (AN-061).
4. Reports the outcome: cost to the ledger (AN-057), failed attempts and their
   transient/terminal classification to the retry log (AN-044), and completion to
   the scheduler so the in-flight gauge that drives backpressure is released.

Node failures map to ``ApplicationError`` with ``non_retryable`` set from the
node's own classification, so transient errors retry with backoff and terminal
ones fail fast instead of burning five attempts on a malformed request.

Importing this module registers the built-in nodes and the CI mock LLM provider.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

import ancora.nodes  # noqa: F401 — import registers the built-in node types
from ancora.nodes.base import NodeContext, NodeError
from ancora.nodes.gemini_provider import GeminiProvider
from ancora.nodes.llm import MockProvider, register_provider
from ancora.nodes.registry import get as get_node
from ancora_activity_worker.runtime import get_inbox, get_node_recorder, get_scheduler
from ancora_common import projections

logger = logging.getLogger("ancora.runtime.nodes")

# CI/dev default so LLM nodes work without API keys. Real providers are registered
# at worker startup from configuration.
register_provider(MockProvider("mock"))
register_provider(MockProvider("mock-secondary"))
register_provider(GeminiProvider())


class NodeRequest(dict[str, Any]):
    """Wire shape passed by ``Workflow.call_node``; kept as a plain dict for Temporal."""


def _provider_of(type_name: str, payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Which provider/model this call will hit, for rate-limit bucketing (AN-040).

    The LLM node fans out over a provider chain; the *first* entry is the one it
    will actually try, and therefore the bucket whose quota is at risk. HTTP calls
    bucket by host so a single flaky API cannot be starved by traffic to another.
    """
    if type_name == "llm":
        providers = payload.get("providers") or []
        provider = str(providers[0]) if providers else None
        return provider, payload.get("model")
    if type_name == "http":
        url = str(payload.get("url", ""))
        if "://" in url:
            host = url.split("://", 1)[1].split("/", 1)[0]
            return host or None, None
    return None, None


async def _admit(
    type_name: str, node_id: str, payload: dict[str, Any], req: dict[str, Any]
) -> None:
    """Consult the scheduler; raise if the node must wait or must not run."""
    scheduler = get_scheduler()
    if not getattr(scheduler, "enabled", False):
        return

    sched: dict[str, Any] = dict(req.get("scheduling") or {})
    provider, model = _provider_of(type_name, payload)
    info = activity.info()
    verdict = await scheduler.admit(
        {
            "run_id": str(req.get("workflow_id") or info.workflow_id),
            "node_id": node_id,
            "node_type": type_name,
            "task_queue": sched.get("task_queue") or info.task_queue,
            "tenant": sched.get("tenant", "default"),
            "priority": int(sched.get("priority", 3)),
            "attempt": info.attempt,
            "provider": provider,
            "model": model,
            "deadline_seconds": sched.get("deadline_seconds"),
        }
    )
    if verdict.warning:
        logger.warning("scheduler warning for %s/%s: %s", node_id, type_name, verdict.warning)
    if verdict.admitted:
        return
    if verdict.outcome == "reject":
        raise ApplicationError(
            f"scheduler rejected {node_id}: {verdict.reason}",
            type="SchedulerRejected",
            non_retryable=True,
        )
    # Deferred: hand the work back to Temporal with the scheduler's backoff. This
    # is not a failure — it is durable, zero-cost waiting.
    raise ApplicationError(
        f"scheduler deferred {node_id} ({verdict.rule}): {verdict.reason}",
        type="SchedulerDeferred",
        non_retryable=False,
        next_retry_delay=timedelta(seconds=max(verdict.retry_after, 0.05)),
    )


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
    """Resolve, admit, validate, and execute a built-in node with exactly-once effects."""
    type_name = str(req["type_name"])
    node_id = str(req["node_id"])
    payload: dict[str, Any] = dict(req.get("input") or {})
    key = str(req["idempotency_key"])
    wf_id = str(req.get("workflow_id") or activity.info().workflow_id)
    tenant = str((req.get("scheduling") or {}).get("tenant", "default"))
    attempt = activity.info().attempt

    # Admission runs before anything else — including the projection write below —
    # so a deferred node leaves no trace of having started.
    await _admit(type_name, node_id, payload, req)

    node_cls = get_node(type_name)
    recorder = get_node_recorder()
    await recorder.record_start(
        {
            "temporal_wf_id": wf_id,
            "node_name": type_name,
            "capability": "io",
            "backend": "node",
            "ray_task_id": None,
            "attempt": attempt,
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
        await projections.record_retry(
            temporal_wf_id=wf_id,
            node_id=node_id,
            node_type=type_name,
            attempt=attempt,
            error=str(exc),
            transient=exc.transient,
            retry_after=exc.retry_after,
            worker_id=activity.info().activity_id,
        )
        # The slot is held per (run, node); release it so a failed node does not
        # count against the queue's depth while it waits to be retried.
        await get_scheduler().complete({"run_id": wf_id, "node_id": node_id, "tenant": tenant})
        # Terminal node errors must not retry; transient ones should.
        raise ApplicationError(str(exc), type="NodeError", non_retryable=not exc.transient) from exc

    await recorder.record_finish(
        {"temporal_wf_id": wf_id, "node_name": type_name, "status": "Completed"}
    )
    cost: dict[str, Any] = dict(result.get("cost") or {})
    await projections.record_cost(
        temporal_wf_id=wf_id,
        node_id=node_id,
        node_type=type_name,
        attempt=attempt,
        cost=cost,
    )
    await get_scheduler().complete(
        {
            "run_id": wf_id,
            "node_id": node_id,
            "tenant": tenant,
            "usd": float(cost.get("usd", 0.0) or 0.0),
        }
    )
    return result
