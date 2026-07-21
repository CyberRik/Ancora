"""Workflow catalog + run-projection helpers, shared by the API and workers.

The catalog (``workflow_def``/``workflow_version``) is authoritative app metadata.
The run projection (``workflow_run``) is a *derived* view of Temporal state; in
Phase 1 it is refreshed by polling Temporal (crude but correct), and replaced by
an event-sourced consumer in Phase 4.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ancora_common.models import WorkflowDef, WorkflowRun, WorkflowVersion


class AncoraRunStatus:
    """Ancora-facing run states (a superset projection over Temporal's)."""

    QUEUED: Final = "Queued"
    RUNNING: Final = "Running"
    COMPLETED: Final = "Completed"
    FAILED: Final = "Failed"
    CANCELLED: Final = "Cancelled"
    TERMINATED: Final = "Terminated"
    TIMED_OUT: Final = "TimedOut"

    TERMINAL: Final = frozenset({COMPLETED, FAILED, CANCELLED, TERMINATED, TIMED_OUT})


# Temporal WorkflowExecutionStatus int values → Ancora status.
# (Values per the Temporal proto enum; kept explicit to avoid importing enums
# into modules that may run under the workflow sandbox.)
_TEMPORAL_STATUS_MAP: Final[dict[int, str]] = {
    1: AncoraRunStatus.RUNNING,  # RUNNING
    2: AncoraRunStatus.COMPLETED,  # COMPLETED
    3: AncoraRunStatus.FAILED,  # FAILED
    4: AncoraRunStatus.CANCELLED,  # CANCELED
    5: AncoraRunStatus.TERMINATED,  # TERMINATED
    6: AncoraRunStatus.RUNNING,  # CONTINUED_AS_NEW → still logically running
    7: AncoraRunStatus.TIMED_OUT,  # TIMED_OUT
}


def map_temporal_status(status_value: int) -> str:
    return _TEMPORAL_STATUS_MAP.get(status_value, AncoraRunStatus.RUNNING)


# --------------------------------------------------------------------------- #
# Catalog (definitions + versions)
# --------------------------------------------------------------------------- #
async def get_workflow_def(
    session: AsyncSession, *, project_id: uuid.UUID, name: str
) -> WorkflowDef | None:
    result = await session.execute(
        select(WorkflowDef).where(WorkflowDef.project_id == project_id, WorkflowDef.name == name)
    )
    return result.scalar_one_or_none()


async def list_workflow_defs(session: AsyncSession, *, project_id: uuid.UUID) -> list[WorkflowDef]:
    result = await session.execute(
        select(WorkflowDef).where(WorkflowDef.project_id == project_id).order_by(WorkflowDef.name)
    )
    return list(result.scalars().all())


async def get_latest_version(
    session: AsyncSession, *, workflow_def_id: uuid.UUID
) -> WorkflowVersion | None:
    result = await session.execute(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_def_id == workflow_def_id)
        .order_by(WorkflowVersion.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def register_workflow(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    name: str,
    dag_spec: dict[str, Any],
    code_hash: str,
    determinism_token: str,
    task_queue: str,
) -> WorkflowVersion:
    """Upsert a workflow definition and version.

    Idempotent on code: if the latest version already has this ``code_hash`` it is
    returned unchanged; otherwise a new monotonically increasing version is created.
    """
    wf_def = await get_workflow_def(session, project_id=project_id, name=name)
    if wf_def is None:
        wf_def = WorkflowDef(project_id=project_id, name=name)
        session.add(wf_def)
        await session.flush()

    latest = await get_latest_version(session, workflow_def_id=wf_def.id)
    if latest is not None and latest.code_hash == code_hash:
        return latest

    version = WorkflowVersion(
        workflow_def_id=wf_def.id,
        version=(latest.version + 1) if latest else 1,
        dag_spec=dag_spec,
        code_hash=code_hash,
        determinism_token=determinism_token,
        task_queue=task_queue,
    )
    session.add(version)
    await session.flush()
    return version


# --------------------------------------------------------------------------- #
# Run projection
# --------------------------------------------------------------------------- #
async def create_run(
    session: AsyncSession,
    *,
    workflow_version_id: uuid.UUID,
    temporal_wf_id: str,
    temporal_run_id: str,
    run_input: dict[str, Any] | None,
    status: str = AncoraRunStatus.QUEUED,
    started_at: datetime | None = None,
) -> WorkflowRun:
    run = WorkflowRun(
        workflow_version_id=workflow_version_id,
        temporal_wf_id=temporal_wf_id,
        temporal_run_id=temporal_run_id,
        status=status,
        input=run_input,
        started_at=started_at,
    )
    session.add(run)
    await session.flush()
    return run


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> WorkflowRun | None:
    return await session.get(WorkflowRun, run_id)


async def get_run_by_temporal_wf_id(
    session: AsyncSession, temporal_wf_id: str
) -> WorkflowRun | None:
    result = await session.execute(
        select(WorkflowRun).where(WorkflowRun.temporal_wf_id == temporal_wf_id)
    )
    return result.scalars().first()


async def list_runs(session: AsyncSession, *, limit: int = 100) -> list[WorkflowRun]:
    result = await session.execute(
        select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())
