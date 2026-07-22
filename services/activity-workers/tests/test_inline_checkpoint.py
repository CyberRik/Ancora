"""Model A (inline) + heartbeat-checkpoint resume (AN-027, AN-029).

The first attempt of a 100-batch activity crashes at batch 40 after checkpointing.
Temporal retries it; the retry reads the last heartbeat and resumes at 40, not 0.
We prove that by asserting ``resumed_from == 40`` while the final checksum still
equals a full, correct run (no batches skipped, none double-counted).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from temporalio.common import RetryPolicy
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ancora import Workflow, workflow
from ancora_activity_worker.activities import ACTIVITIES, ray_compute
from ancora_activity_worker.tasks import ComputeRequest, ComputeResult

pytestmark = pytest.mark.temporal

_TASK_QUEUE = "tq-inline"


@workflow.defn
class InlineComputeWorkflow(Workflow):
    @workflow.run
    async def run(self, req: ComputeRequest) -> ComputeResult:
        return await self.call(
            ray_compute,
            req,
            start_to_close_timeout=timedelta(seconds=60),
            heartbeat_timeout=timedelta(seconds=10),
            retry=RetryPolicy(
                maximum_attempts=5,
                initial_interval=timedelta(milliseconds=100),
            ),
        )


async def test_inline_compute_completes(env: WorkflowEnvironment) -> None:
    req = ComputeRequest(label="inline", batches=6, batch_seconds=0.01)
    async with Worker(
        env.client,
        task_queue=_TASK_QUEUE,
        workflows=[InlineComputeWorkflow],
        activities=ACTIVITIES,
    ):
        result = await env.client.execute_workflow(
            InlineComputeWorkflow.run, req, id="wf-inline-ok", task_queue=_TASK_QUEUE
        )
    # checksum = 7 * sum(1..6) = 7 * 21 = 147
    assert result.checksum == 147
    assert result.resumed_from == 0


async def test_checkpoint_resume_after_crash(env: WorkflowEnvironment) -> None:
    req = ComputeRequest(
        label="ckpt",
        batches=100,
        batch_seconds=0.01,
        fail_at_batch=40,
        fail_hold=0.3,  # hold after checkpointing so the heartbeat flushes
    )
    async with Worker(
        env.client,
        task_queue=_TASK_QUEUE,
        workflows=[InlineComputeWorkflow],
        activities=ACTIVITIES,
    ):
        result = await env.client.execute_workflow(
            InlineComputeWorkflow.run, req, id="wf-ckpt", task_queue=_TASK_QUEUE
        )
    # The retry resumed at batch 40 (proof the checkpoint survived the crash)...
    assert result.resumed_from == 40
    # ...and the result is still a full, correct run: 7 * sum(1..100) = 7 * 5050.
    assert result.checksum == 35350
