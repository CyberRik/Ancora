"""Workflow service — the boundary between the HTTP API and Temporal + the DB.

Starts/queries/cancels Temporal workflows and maintains the ``workflow_run``
projection. In Phase 1 run status is refreshed by polling Temporal on read
(``describe``); Phase 4 replaces this with an event-sourced consumer.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from temporalio.api.history.v1 import HistoryEvent
from temporalio.api.workflow.v1 import PendingActivityInfo, PendingWorkflowTaskInfo
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import WorkflowAlreadyStartedError

from ancora_api.graph import build_graph
from ancora_api.recovery import FleetLiveness, build_recovery
from ancora_api.schemas import (
    RunActivityOut,
    RunGraphOut,
    RunLiveOut,
    RunOut,
    RunRecoveryOut,
    StartRunRequest,
    WorkflowDefOut,
)
from ancora_common import DEFAULT_PROJECT_ID
from ancora_common.catalog import (
    AncoraRunStatus,
    create_run,
    get_latest_version,
    get_run_by_temporal_wf_id,
    get_workflow_def,
    list_workflow_defs,
    map_temporal_status,
)
from ancora_common.db import session_scope
from ancora_common.models import ApprovalGate, WorkflowRun, WorkflowVersion


class NotFoundError(Exception):
    """Raised when a requested workflow/run does not exist."""


# Temporal ``PendingActivityState`` enum values → readable labels.
_PENDING_ACTIVITY_STATE: dict[int, str] = {1: "Scheduled", 2: "Started", 3: "CancelRequested"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _load_run(session: AsyncSession, run_id: uuid.UUID) -> WorkflowRun | None:
    result = await session.execute(
        select(WorkflowRun)
        .options(
            selectinload(WorkflowRun.workflow_version).selectinload(WorkflowVersion.workflow_def)
        )
        .where(WorkflowRun.id == run_id)
    )
    return result.scalar_one_or_none()


@dataclass
class _Execution:
    """One run's full Temporal record: its history plus what is pending on it now.

    History says what happened; ``describe`` says what is happening. Both views
    built on this need both, because Temporal writes ``ActivityTaskStarted`` only
    at an attempt's terminal event — the attempt in flight is in neither the
    history nor any projection, only in pending state.
    """

    status: str
    terminal: bool
    workflow_name: str
    events: list[HistoryEvent] = field(default_factory=list)
    pending_activities: list[PendingActivityInfo] = field(default_factory=list)
    pending_workflow_task: PendingWorkflowTaskInfo | None = None
    task_queue: str | None = None
    task_timeout: float | None = None


def _to_out(run: WorkflowRun) -> RunOut:
    version = run.workflow_version
    return RunOut(
        id=run.id,
        workflow_name=version.workflow_def.name,
        version=version.version,
        temporal_wf_id=run.temporal_wf_id,
        temporal_run_id=run.temporal_run_id,
        status=run.status,
        input=run.input,
        output=run.output,
        error=run.error,
        started_at=run.started_at,
        closed_at=run.closed_at,
        created_at=run.created_at,
    )


class WorkflowService:
    def __init__(self, client: Client) -> None:
        self.client = client

    # ---- catalog -------------------------------------------------------- #
    async def list_defs(self) -> list[WorkflowDefOut]:
        async with session_scope() as session:
            defs = await list_workflow_defs(session, project_id=DEFAULT_PROJECT_ID)
            out: list[WorkflowDefOut] = []
            for d in defs:
                # versions relationship is ordered by version.
                versions = [v.version for v in d.versions]
                out.append(
                    WorkflowDefOut(
                        id=d.id,
                        name=d.name,
                        latest_version=max(versions) if versions else None,
                        versions=versions,
                        created_at=d.created_at,
                    )
                )
            return out

    # ---- runs ----------------------------------------------------------- #
    async def start_run(
        self, name: str, req: StartRunRequest, idempotency_key: str | None
    ) -> RunOut:
        async with session_scope() as session:
            wf_def = await get_workflow_def(session, project_id=DEFAULT_PROJECT_ID, name=name)
            if wf_def is None:
                raise NotFoundError(f"workflow '{name}' is not registered")

            if req.version is not None:
                res = await session.execute(
                    select(WorkflowVersion).where(
                        WorkflowVersion.workflow_def_id == wf_def.id,
                        WorkflowVersion.version == req.version,
                    )
                )
                version = res.scalar_one_or_none()
            else:
                version = await get_latest_version(session, workflow_def_id=wf_def.id)
            if version is None:
                raise NotFoundError(f"no runnable version for workflow '{name}'")
            version_id = version.id
            task_queue = version.task_queue

        wf_id = f"{name}-{idempotency_key or uuid.uuid4().hex}"
        run_input = req.input or {}

        try:
            handle = await self.client.start_workflow(
                name, run_input, id=wf_id, task_queue=task_queue
            )
        except WorkflowAlreadyStartedError:
            # Idempotency: same key → return the existing run.
            async with session_scope() as session:
                existing = await get_run_by_temporal_wf_id(session, wf_id)
                if existing is not None:
                    loaded = await _load_run(session, existing.id)
                    assert loaded is not None
                    return _to_out(loaded)
            raise

        async with session_scope() as session:
            run = await create_run(
                session,
                workflow_version_id=version_id,
                temporal_wf_id=wf_id,
                temporal_run_id=handle.first_execution_run_id or "",
                run_input=run_input,
                status=AncoraRunStatus.RUNNING,
                started_at=_utcnow(),
            )
            run_id = run.id

        return await self.get_run(run_id)

    async def get_run(self, run_id: uuid.UUID) -> RunOut:
        async with session_scope() as session:
            run = await _load_run(session, run_id)
            if run is None:
                raise NotFoundError(f"run '{run_id}' not found")
            if run.status not in AncoraRunStatus.TERMINAL:
                await self._refresh(run)
            return _to_out(run)

    async def list_runs(self, limit: int = 100) -> list[RunOut]:
        async with session_scope() as session:
            result = await session.execute(
                select(WorkflowRun)
                .options(
                    selectinload(WorkflowRun.workflow_version).selectinload(
                        WorkflowVersion.workflow_def
                    )
                )
                .order_by(WorkflowRun.created_at.desc())
                .limit(limit)
            )
            runs = list(result.scalars().all())
            # Refresh non-terminal runs so the list isn't stale.
            for run in runs:
                if run.status not in AncoraRunStatus.TERMINAL:
                    await self._refresh(run)
            return [_to_out(r) for r in runs]

    async def cancel_run(self, run_id: uuid.UUID) -> RunOut:
        async with session_scope() as session:
            run = await _load_run(session, run_id)
            if run is None:
                raise NotFoundError(f"run '{run_id}' not found")
            handle = self.client.get_workflow_handle(run.temporal_wf_id, run_id=run.temporal_run_id)
            await handle.terminate("Cancelled via API")
            await self._refresh(run)
            return _to_out(run)

    async def signal_run(self, run_id: uuid.UUID, name: str, arg: Any = None) -> RunOut:
        """Deliver a signal to a running workflow (e.g. ``approve`` a durable gate).

        Signals are how a durably-waiting workflow is advanced from the outside —
        the human-in-the-loop path (RFC-0001 §12). The workflow may have been
        parked for seconds or days, across worker restarts; the signal resumes it.
        """
        async with session_scope() as session:
            run = await _load_run(session, run_id)
            if run is None:
                raise NotFoundError(f"run '{run_id}' not found")
            handle = self.client.get_workflow_handle(run.temporal_wf_id, run_id=run.temporal_run_id)
            await handle.signal(name) if arg is None else await handle.signal(name, arg)
            await self._refresh(run)
            return _to_out(run)

    async def get_run_live(self, run_id: uuid.UUID) -> RunLiveOut:
        """Real-time activity state for a run, straight from Temporal (demo money-shot).

        Reads ``describe()``'s pending activities so the UI can show the *actual*
        attempt counter ticking (1 → 2 when a worker dies and the step is retried)
        and the real failure — not a simulated animation.
        """
        async with session_scope() as session:
            run = await _load_run(session, run_id)
            if run is None:
                raise NotFoundError(f"run '{run_id}' not found")
            if run.status not in AncoraRunStatus.TERMINAL:
                await self._refresh(run)
            status = run.status
            wf_id = run.temporal_wf_id
            temporal_run_id = run.temporal_run_id

        handle = self.client.get_workflow_handle(wf_id, run_id=temporal_run_id)
        activities: list[RunActivityOut] = []
        try:
            desc = await handle.describe()
            for pa in desc.raw_description.pending_activities:
                last_failure = pa.last_failure.message if pa.HasField("last_failure") else None
                activities.append(
                    RunActivityOut(
                        activity_id=pa.activity_id,
                        activity_type=pa.activity_type.name,
                        state=_PENDING_ACTIVITY_STATE.get(int(pa.state), "Unknown"),
                        attempt=int(pa.attempt),
                        maximum_attempts=int(pa.maximum_attempts),
                        last_failure=last_failure or None,
                        last_worker_identity=pa.last_worker_identity or None,
                    )
                )
        except Exception:  # noqa: BLE001 — best-effort live view; never 500 the demo
            pass

        status_note: str | None = None
        if status not in AncoraRunStatus.TERMINAL:
            try:
                status_note = await handle.query("current_status")
            except Exception:  # noqa: BLE001 — workflow may not expose the query
                async with session_scope() as session:
                    gate = (
                        await session.execute(
                            select(ApprovalGate).where(
                                ApprovalGate.temporal_wf_id == wf_id,
                                ApprovalGate.status == "waiting",
                            )
                        )
                    ).scalar_one_or_none()
                    if gate:
                        status_note = f"Awaiting human approval (gate_id={gate.gate_id})..."
                    else:
                        status_note = None

        return RunLiveOut(
            run_id=run_id, status=status, status_note=status_note, activities=activities
        )

    async def _read_execution(self, run_id: uuid.UUID) -> _Execution:
        """Fetch a run's full history plus its live pending state.

        Shared by the two whole-history views (recovery and graph) and kept off
        the hot ``/activities`` poll, which must stay cheap. Phase 4's event
        consumer replaces the fetch with a projection.
        """
        async with session_scope() as session:
            run = await _load_run(session, run_id)
            if run is None:
                raise NotFoundError(f"run '{run_id}' not found")
            if run.status not in AncoraRunStatus.TERMINAL:
                await self._refresh(run)
            execution = _Execution(
                status=run.status,
                terminal=run.status in AncoraRunStatus.TERMINAL,
                workflow_name=run.workflow_version.workflow_def.name,
            )
            wf_id = run.temporal_wf_id
            temporal_run_id = run.temporal_run_id

        handle = self.client.get_workflow_handle(wf_id, run_id=temporal_run_id)
        execution.events = [e async for e in handle.fetch_history_events()]

        try:
            raw = (await handle.describe()).raw_description
            execution.pending_activities = list(raw.pending_activities)
            if raw.HasField("pending_workflow_task"):
                execution.pending_workflow_task = raw.pending_workflow_task
            config = raw.execution_config
            execution.task_queue = config.task_queue.name or None
            if config.HasField("default_workflow_task_timeout"):
                execution.task_timeout = (
                    config.default_workflow_task_timeout.ToTimedelta().total_seconds()
                )
        except Exception:  # noqa: BLE001 — the historical views stand on their own
            pass
        return execution

    async def get_run_recovery(
        self,
        run_id: uuid.UUID,
        *,
        chaos_events: Sequence[dict[str, Any]] = (),
        liveness: FleetLiveness | None = None,
    ) -> RunRecoveryOut:
        """Reconstruct what a kill did to this run, and what it is waiting on now."""
        ex = await self._read_execution(run_id)
        return build_recovery(
            run_id=run_id,
            status=ex.status,
            events=ex.events,
            pending_activities=ex.pending_activities,
            pending_workflow_task=ex.pending_workflow_task,
            workflow_task_queue=ex.task_queue,
            workflow_task_timeout=ex.task_timeout,
            chaos_events=chaos_events,
            liveness=liveness,
        )

    async def get_run_graph(self, run_id: uuid.UUID) -> RunGraphOut:
        """The DAG this run executed, with each vertex's state right now."""
        ex = await self._read_execution(run_id)
        return build_graph(
            run_id=run_id,
            workflow_name=ex.workflow_name,
            status=ex.status,
            terminal=ex.terminal,
            events=ex.events,
            pending_activities=ex.pending_activities,
            has_pending_workflow_task=ex.pending_workflow_task is not None,
        )

    # ---- internal: crude status projection ------------------------------ #
    async def _refresh(self, run: WorkflowRun) -> None:
        """Update a run row from live Temporal state (Phase 1 polling projection).

        Caller must hold an active session containing ``run``.
        """
        handle = self.client.get_workflow_handle(run.temporal_wf_id, run_id=run.temporal_run_id)
        desc = await handle.describe()
        status = map_temporal_status(int(desc.status)) if desc.status else run.status
        run.status = status
        if status in AncoraRunStatus.TERMINAL:
            run.closed_at = desc.close_time
            if status == AncoraRunStatus.COMPLETED:
                output = await handle.result()
                run.output = output if isinstance(output, dict) else {"result": output}
            elif status == AncoraRunStatus.FAILED:
                try:
                    await handle.result()
                except WorkflowFailureError as exc:
                    run.error = str(exc.cause or exc)
                except Exception as exc:  # noqa: BLE001
                    run.error = str(exc)
