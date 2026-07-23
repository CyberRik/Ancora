"""Human-gate expiry branch — RFC-0001a §8 scenario 12 (AN-067).

A gate that nobody answers must not park a workflow forever. These tests prove
the expiry branch actually runs, and that it runs *only* when no decision arrives.

The multi-day wait is simulated with Temporal's time-skipping test server: while
the workflow is idle on a timer, the server jumps its clock forward. The three
days elapse in milliseconds and the code path exercised is the real one — the
same durable timer that would fire in production over a long weekend.
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

from ancora_common import projections
from ancora_worker.examples import HumanGateWorkflow, greet
from ancora_worker.gate_activities import GATE_ACTIVITIES

pytestmark = pytest.mark.temporal

_QUEUE = "tq-human-gate"


@pytest.fixture(autouse=True)
def _no_projection() -> Any:
    # No database here; the gate index is reporting only.
    projections.set_enabled(False)
    yield
    projections.set_enabled(True)


@pytest_asyncio.fixture
async def skipping_env() -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    try:
        yield env
    finally:
        await env.shutdown()


@pytest_asyncio.fixture
async def local_env() -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter)
    try:
        yield env
    finally:
        await env.shutdown()


def _worker(env: WorkflowEnvironment) -> Worker:
    return Worker(
        env.client,
        task_queue=_QUEUE,
        workflows=[HumanGateWorkflow],
        activities=[greet, *GATE_ACTIVITIES],
    )


async def test_a_three_day_gate_expires_and_escalates(
    skipping_env: WorkflowEnvironment,
) -> None:
    async with _worker(skipping_env):
        result = await skipping_env.client.execute_workflow(
            HumanGateWorkflow.run,
            {"expiry_days": 3, "release": "v2.1.0"},
            id="wf-gate-expiry",
            task_queue=_QUEUE,
        )

    assert result["status"] == "expired"
    assert result["branch"] == "escalated"
    assert result["waited_days"] == 3
    # The escalation activity ran *after* the expiry — the branch is real work,
    # not just a returned literal.
    assert "v2.1.0" in result["escalation"]


async def test_an_approval_before_expiry_takes_the_decided_branch(
    local_env: WorkflowEnvironment,
) -> None:
    # Time-skipping cannot be used here: it would race the timer past the signal.
    async with _worker(local_env):
        handle = await local_env.client.start_workflow(
            HumanGateWorkflow.run,
            {"expiry_days": 3},
            id="wf-gate-approved",
            task_queue=_QUEUE,
        )
        # Give the workflow a moment to reach the gate, then decide.
        await asyncio.sleep(0.5)
        await handle.signal(
            "submit_decision",
            {"gate_id": "release", "approved": True, "comment": "ship it"},
        )
        result = await handle.result()

    assert result["status"] == "approved"
    assert result["branch"] == "decided"
    assert result["comment"] == "ship it"


async def test_a_rejection_before_expiry_is_not_an_expiry(
    local_env: WorkflowEnvironment,
) -> None:
    async with _worker(local_env):
        handle = await local_env.client.start_workflow(
            HumanGateWorkflow.run,
            {"expiry_days": 3},
            id="wf-gate-rejected",
            task_queue=_QUEUE,
        )
        await asyncio.sleep(0.5)
        await handle.signal(
            "submit_decision",
            {"gate_id": "release", "approved": False, "comment": "not this week"},
        )
        result = await handle.result()

    # An explicit "no" must be distinguishable from "nobody answered": they lead
    # to different places (rejection stops; expiry escalates).
    assert result["status"] == "rejected"
    assert result["branch"] == "decided"
