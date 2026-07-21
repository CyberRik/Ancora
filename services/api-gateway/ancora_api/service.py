"""Workflow service — the boundary between the HTTP API and Temporal + the DB.

Starts/queries/cancels Temporal workflows and maintains the ``workflow_run``
projection. In Phase 1 run status is refreshed by polling Temporal on read
(``describe``); Phase 4 replaces this with an event-sourced consumer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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
from ancora_common.models import WorkflowRun, WorkflowVersion
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import WorkflowAlreadyStartedError

from ancora_api.schemas import RunOut, StartRunRequest, WorkflowDefOut


class NotFoundError(Exception):
    """Raised when a requested workflow/run does not exist."""


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
            await handle.cancel()
            await self._refresh(run)
            return _to_out(run)

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
