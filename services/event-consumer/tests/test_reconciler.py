"""Unit tests for the reconciler's status transitions and event emission."""

from __future__ import annotations

from typing import Any

import pytest
from ancora_event_consumer.reconciler import Reconciler
from ancora_event_consumer.settings import ConsumerSettings

from ancora_common.catalog import AncoraRunStatus
from ancora_common.events import EventKind, RunEvent
from ancora_common.models import WorkflowRun


class _FakeDesc:
    def __init__(self, status: int, close_time: Any = None) -> None:
        self.status = status
        self.close_time = close_time


class _FakeHandle:
    def __init__(self, status: int, result: Any = None, failure: Exception | None = None) -> None:
        self._status = status
        self._result = result
        self._failure = failure

    async def describe(self) -> _FakeDesc:
        return _FakeDesc(self._status)

    async def result(self) -> Any:
        if self._failure is not None:
            raise self._failure
        return self._result


class _FakeClient:
    def __init__(self, handle: _FakeHandle) -> None:
        self._handle = handle

    def get_workflow_handle(self, wf_id: str, run_id: str) -> _FakeHandle:
        return self._handle


class _RecordingBus:
    def __init__(self) -> None:
        self.published: list[RunEvent] = []

    async def publish(self, event: RunEvent) -> None:
        self.published.append(event)


def _run() -> WorkflowRun:
    run = WorkflowRun()
    run.temporal_wf_id = "wf-1"
    run.temporal_run_id = "run-1"
    run.status = AncoraRunStatus.RUNNING
    return run


@pytest.mark.asyncio
async def test_unchanged_status_is_a_noop() -> None:
    bus = _RecordingBus()
    rec = Reconciler(_FakeClient(_FakeHandle(1)), bus, ConsumerSettings())  # type: ignore[arg-type]
    run = _run()
    became = await rec._reconcile_run(run)
    assert became is False
    assert run.status == AncoraRunStatus.RUNNING
    assert bus.published == []


@pytest.mark.asyncio
async def test_completion_captures_output_and_emits_event() -> None:
    bus = _RecordingBus()
    handle = _FakeHandle(2, result={"answer": 42})  # 2 == COMPLETED
    rec = Reconciler(_FakeClient(handle), bus, ConsumerSettings())  # type: ignore[arg-type]
    run = _run()
    became = await rec._reconcile_run(run)
    assert became is True
    assert run.status == AncoraRunStatus.COMPLETED
    assert run.output == {"answer": 42}
    assert [e.kind for e in bus.published] == [EventKind.RUN_COMPLETED]


@pytest.mark.asyncio
async def test_non_dict_result_is_wrapped() -> None:
    bus = _RecordingBus()
    handle = _FakeHandle(2, result="a plain string")
    rec = Reconciler(_FakeClient(handle), bus, ConsumerSettings())  # type: ignore[arg-type]
    run = _run()
    await rec._reconcile_run(run)
    assert run.output == {"result": "a plain string"}


@pytest.mark.asyncio
async def test_failure_captures_error_and_emits_run_failed() -> None:
    bus = _RecordingBus()
    handle = _FakeHandle(3, failure=RuntimeError("provider exhausted"))  # 3 == FAILED
    rec = Reconciler(_FakeClient(handle), bus, ConsumerSettings())  # type: ignore[arg-type]
    run = _run()
    became = await rec._reconcile_run(run)
    assert became is True
    assert run.status == AncoraRunStatus.FAILED
    assert run.error is not None and "provider exhausted" in run.error
    assert [e.kind for e in bus.published] == [EventKind.RUN_FAILED]
