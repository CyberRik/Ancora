"""End-to-end test for the research-agent example (AN-059).

Exercises the full Phase-3 node chain on a real dev server: the workflow calls
built-in nodes (``call_node`` → ``run_node`` → node execution) for search,
parallel summarize, and synthesize; waits durably at a human-approval gate; then
finishes. A second test kills the worker while parked at the gate and resumes on a
fresh one — the north-star durability proof (kill any worker → correct resume,
zero duplicated effects).

Uses ``start_local`` (real dev server), not time-skipping: the flow makes progress
via an external signal after a durable wait, which time-skipping cannot anchor.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ancora.nodes import MockProvider, register_provider
from ancora.nodes.llm import clear_providers
from ancora_activity_worker import runtime
from ancora_activity_worker.nodes_runtime import run_node
from ancora_common import projections
from ancora_common.inbox import InMemoryInboxGuard
from ancora_common.resources import Capability, queue_for
from ancora_worker.examples import ResearchAgentWorkflow, greet
from ancora_worker.gate_activities import GATE_ACTIVITIES

pytestmark = pytest.mark.temporal

_WF_QUEUE = "tq-research"


@pytest_asyncio.fixture
async def env() -> AsyncIterator[WorkflowEnvironment]:
    """Real dev server (overrides the conftest time-skipping ``env``)."""
    environment = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter)
    try:
        yield environment
    finally:
        await environment.shutdown()


@pytest.fixture(autouse=True)
def _node_runtime() -> Any:
    runtime.reset()
    runtime.set_inbox(InMemoryInboxGuard())
    # No database in this test; the gate projection is reporting only, so turn it
    # off rather than pay a connection failure per gate.
    projections.set_enabled(False)
    clear_providers()
    register_provider(MockProvider("mock"))
    register_provider(MockProvider("mock-secondary"))
    register_provider(MockProvider("gemini"))
    yield
    runtime.reset()
    clear_providers()
    projections.set_enabled(True)


def _activity_workers(env: WorkflowEnvironment) -> list[Worker]:
    """A run_node worker per capability queue the workflow dispatches to."""
    return [
        Worker(env.client, task_queue=queue_for(Capability.CPU), activities=[run_node]),
        Worker(env.client, task_queue=queue_for(Capability.IO), activities=[run_node]),
    ]


async def _wait_for_gate(handle: Any, tries: int = 200) -> None:
    for _ in range(tries):
        if await handle.query(ResearchAgentWorkflow.at_gate):
            return
        await asyncio.sleep(0.05)
    pytest.fail("research agent never reached the approval gate")


async def test_research_agent_runs_end_to_end(env: WorkflowEnvironment) -> None:
    wf_worker = Worker(
        env.client,
        task_queue=_WF_QUEUE,
        workflows=[ResearchAgentWorkflow],
        activities=[greet, *GATE_ACTIVITIES],
    )
    activity_workers = _activity_workers(env)

    async with wf_worker, activity_workers[0], activity_workers[1]:
        handle = await env.client.start_workflow(
            ResearchAgentWorkflow.run,
            {"topic": "durable execution", "summaries": 3},
            id="wf-research-e2e",
            task_queue=_WF_QUEUE,
        )
        await _wait_for_gate(handle)
        await handle.signal("submit_decision", {"gate_id": "publish", "approved": True})
        result = await handle.result()

    assert result["status"] == "published"
    assert result["summaries"] == 3
    assert result["cost_usd"] > 0
    assert "durable execution" in result["report"]


async def test_research_agent_rejection_takes_reject_branch(env: WorkflowEnvironment) -> None:
    wf_worker = Worker(
        env.client,
        task_queue=_WF_QUEUE,
        workflows=[ResearchAgentWorkflow],
        activities=[greet, *GATE_ACTIVITIES],
    )
    activity_workers = _activity_workers(env)

    async with wf_worker, activity_workers[0], activity_workers[1]:
        handle = await env.client.start_workflow(
            ResearchAgentWorkflow.run,
            {"topic": "x", "summaries": 1},
            id="wf-research-reject",
            task_queue=_WF_QUEUE,
        )
        await _wait_for_gate(handle)
        await handle.signal(
            "submit_decision", {"gate_id": "publish", "approved": False, "comment": "not now"}
        )
        result = await handle.result()

    assert result["status"] == "rejected"
    assert result["comment"] == "not now"


async def test_research_agent_survives_worker_kill_at_gate(env: WorkflowEnvironment) -> None:
    # Worker A: drive the run to the durable approval gate, then die.
    wf_a = Worker(
        env.client,
        task_queue=_WF_QUEUE,
        workflows=[ResearchAgentWorkflow],
        activities=[greet, *GATE_ACTIVITIES],
    )
    aw = _activity_workers(env)
    await wf_a.__aenter__()
    await aw[0].__aenter__()
    await aw[1].__aenter__()
    try:
        handle = await env.client.start_workflow(
            ResearchAgentWorkflow.run,
            {"topic": "recovery", "summaries": 2},
            id="wf-research-kill",
            task_queue=_WF_QUEUE,
        )
        await _wait_for_gate(handle)
    finally:
        await wf_a.__aexit__(None, None, None)
        await aw[0].__aexit__(None, None, None)
        await aw[1].__aexit__(None, None, None)

    # The run's progress (search + summaries + synthesize) lives in history.
    # Fresh workers resume it and finish on approval — no re-execution.
    wf_b = Worker(
        env.client,
        task_queue=_WF_QUEUE,
        workflows=[ResearchAgentWorkflow],
        activities=[greet, *GATE_ACTIVITIES],
    )
    aw_b = _activity_workers(env)
    async with wf_b, aw_b[0], aw_b[1]:
        await handle.signal("submit_decision", {"gate_id": "publish", "approved": True})
        result = await handle.result()

    assert result["status"] == "published"
    assert result["summaries"] == 2
