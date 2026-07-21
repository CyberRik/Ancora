"""Integration test: the hello workflow runs its 3 activities to completion (AN-016)."""

from __future__ import annotations

import pytest
from ancora_worker.examples import HelloWorkflow, greet
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

pytestmark = pytest.mark.temporal


async def test_hello_completes(env: WorkflowEnvironment) -> None:
    async with Worker(
        env.client,
        task_queue="tq-hello",
        workflows=[HelloWorkflow],
        activities=[greet],
    ):
        result = await env.client.execute_workflow(
            HelloWorkflow.run,
            {"name": "Ada"},
            id="wf-hello-1",
            task_queue="tq-hello",
        )

    # Ada → "Hello, Ada!" → "Hello, Hello, Ada!!" → "Hello, Hello, Hello, Ada!!!"
    assert result["message"] == "Hello, Hello, Hello, Ada!!!"
    assert result["steps"] == 3
