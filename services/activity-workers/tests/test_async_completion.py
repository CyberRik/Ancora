"""Model B (async activity completion) — the dispatcher slot is freed (AN-028).

A worker limited to ``max_concurrent_activities=1`` runs two long async activities
whose compute intervals *overlap*. That overlap is only possible if the first
activity freed its dispatcher slot (via ``raise_complete_async``) while its compute
kept running in the background — the property the whole execution model rests on.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ancora import Workflow, workflow
from ancora_activity_worker import runtime
from ancora_activity_worker.activities import ACTIVITIES, ray_compute_async
from ancora_activity_worker.tasks import ComputeRequest, ComputeResult

pytestmark = pytest.mark.temporal

_TASK_QUEUE = "tq-async"


@workflow.defn
class AsyncComputeWorkflow(Workflow):
    @workflow.run
    async def run(self, req: ComputeRequest) -> ComputeResult:
        return await self.call(
            ray_compute_async, req, start_to_close_timeout=timedelta(seconds=300)
        )


class RecordingRecorder:
    """Timestamps each node's [start, finish] to detect concurrent execution."""

    def __init__(self) -> None:
        self.intervals: dict[str, list[float | None]] = {}

    async def record_start(self, meta: dict[str, Any]) -> None:
        self.intervals.setdefault(meta["temporal_wf_id"], [None, None])[0] = time.monotonic()

    async def record_finish(self, meta: dict[str, Any]) -> None:
        self.intervals.setdefault(meta["temporal_wf_id"], [None, None])[1] = time.monotonic()

    def overlapped(self) -> bool:
        done = [iv for iv in self.intervals.values() if iv[0] is not None and iv[1] is not None]
        if len(done) < 2:
            return False
        (s1, e1), (s2, e2) = done[0], done[1]  # type: ignore[misc]
        return max(s1, s2) < min(e1, e2)


async def test_async_completion_frees_slot(env: WorkflowEnvironment) -> None:
    runtime.set_completion_client(env.client)
    recorder = RecordingRecorder()
    runtime.set_node_recorder(recorder)

    req = ComputeRequest(label="async", batches=8, batch_seconds=0.04)  # ~0.32s of compute

    async with Worker(
        env.client,
        task_queue=_TASK_QUEUE,
        workflows=[AsyncComputeWorkflow],
        activities=ACTIVITIES,
        max_concurrent_activities=1,  # the whole point: 1 slot, 2 concurrent computes
    ):
        h1 = await env.client.start_workflow(
            AsyncComputeWorkflow.run, req, id="wf-async-1", task_queue=_TASK_QUEUE
        )
        h2 = await env.client.start_workflow(
            AsyncComputeWorkflow.run, req, id="wf-async-2", task_queue=_TASK_QUEUE
        )
        r1 = await h1.result()
        r2 = await h2.result()

    # checksum = 7 * sum(1..8) = 7 * 36 = 252
    assert r1.checksum == 252
    assert r2.checksum == 252
    assert recorder.overlapped(), f"computes did not overlap: {recorder.intervals}"
