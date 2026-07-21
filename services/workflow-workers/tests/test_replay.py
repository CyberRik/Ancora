"""Replay test harness (AN-021).

Runs a workflow to completion, captures its history, then replays that history
against the current workflow code. A non-deterministic change would raise here —
this is the real determinism guarantee (RFC-0001a §1.5).
"""

from __future__ import annotations

import pytest
from ancora_worker.examples import HelloWorkflow, greet
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

pytestmark = pytest.mark.temporal


async def test_history_replays_deterministically(env: WorkflowEnvironment) -> None:
    async with Worker(
        env.client,
        task_queue="tq-replay",
        workflows=[HelloWorkflow],
        activities=[greet],
    ):
        handle = await env.client.start_workflow(
            HelloWorkflow.run,
            {"name": "Grace"},
            id="wf-replay-1",
            task_queue="tq-replay",
        )
        await handle.result()

    history = await handle.fetch_history()

    replayer = Replayer(
        workflows=[HelloWorkflow],
        data_converter=pydantic_data_converter,
    )
    # Raises WorkflowNondeterminismError if current code diverges from history.
    await replayer.replay_workflow(history)
