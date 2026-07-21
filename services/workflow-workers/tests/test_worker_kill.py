"""Durability smoke test: kill the worker mid-run, resume on a fresh one (AN-023).

The gated workflow runs one activity, then waits durably for a signal. We:
  1. start it on worker A, wait until the first activity is done (at the gate),
  2. shut worker A down entirely (simulating a crash/redeploy),
  3. bring up worker B and send the signal,
  4. assert the workflow completes correctly.

Because the first activity's result lives in Temporal history, worker B replays
it without re-executing — no lost state, no duplicated work.
"""

from __future__ import annotations

import asyncio

import pytest
from ancora_worker.examples import GatedWorkflow, greet
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

pytestmark = pytest.mark.temporal

_TASK_QUEUE = "tq-durable"


async def _wait_for_gate(handle: object, tries: int = 200) -> None:
    for _ in range(tries):
        if await handle.query(GatedWorkflow.at_gate):  # type: ignore[attr-defined]
            return
        await asyncio.sleep(0.05)
    pytest.fail("workflow never reached the durable gate")


async def test_worker_restart_resumes(env: WorkflowEnvironment) -> None:
    # --- Worker A: start the run and drive it to the durable gate. ---
    worker_a = Worker(
        env.client,
        task_queue=_TASK_QUEUE,
        workflows=[GatedWorkflow],
        activities=[greet],
    )
    await worker_a.__aenter__()
    try:
        handle = await env.client.start_workflow(
            GatedWorkflow.run,
            {"name": "Ada"},
            id="wf-durable-1",
            task_queue=_TASK_QUEUE,
        )
        await _wait_for_gate(handle)
    finally:
        # --- Kill worker A entirely. ---
        await worker_a.__aexit__(None, None, None)

    # Nothing is running the workflow now; its state is durable in history.

    # --- Worker B: resume and finish. ---
    async with Worker(
        env.client,
        task_queue=_TASK_QUEUE,
        workflows=[GatedWorkflow],
        activities=[greet],
    ):
        await handle.signal(GatedWorkflow.approve)
        result = await handle.result()

    # Ada → "Hello, Ada!" → "Hello, Hello, Ada!"
    assert result["message"] == "Hello, Hello, Ada!"
