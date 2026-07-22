"""SQLAlchemy ORM models.

Phase 0 introduced the tenancy roots (org/project/user). Phase 1 adds the
workflow catalog (``workflow_def`` → ``workflow_version``) and the run projection
(``workflow_run``). The run projection is *derived* from Temporal — Temporal's
history remains the source of truth (RFC-0001a §4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all Ancora ORM models."""


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# --------------------------------------------------------------------------- #
# Tenancy roots
# --------------------------------------------------------------------------- #
class Org(Base):
    __tablename__ = "org"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = _created_at()

    projects: Mapped[list[Project]] = relationship(
        back_populates="org", cascade="all, delete-orphan"
    )
    users: Mapped[list[User]] = relationship(back_populates="org", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "project"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_project_org_name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    org: Mapped[Org] = relationship(back_populates="projects")
    workflow_defs: Mapped[list[WorkflowDef]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "user"
    __table_args__ = (UniqueConstraint("org_id", "email", name="uq_user_org_email"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    org: Mapped[Org] = relationship(back_populates="users")


# --------------------------------------------------------------------------- #
# Workflow catalog
# --------------------------------------------------------------------------- #
class WorkflowDef(Base):
    __tablename__ = "workflow_def"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_workflow_def_project_name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    project: Mapped[Project] = relationship(back_populates="workflow_defs")
    versions: Mapped[list[WorkflowVersion]] = relationship(
        back_populates="workflow_def",
        cascade="all, delete-orphan",
        order_by="WorkflowVersion.version",
    )


class WorkflowVersion(Base):
    __tablename__ = "workflow_version"
    __table_args__ = (
        UniqueConstraint("workflow_def_id", "version", name="uq_workflow_version_def_version"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workflow_def_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workflow_def.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Declarative DAG spec (imperative workflows store a lightweight descriptor).
    dag_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Content-address of the workflow code; a change bumps the version.
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Temporal build/version id used to guarantee safe replay of a given version.
    determinism_token: Mapped[str] = mapped_column(Text, nullable=False)
    # The task queue the workflow's workers poll (Phase 1: single queue).
    task_queue: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    workflow_def: Mapped[WorkflowDef] = relationship(back_populates="versions")
    runs: Mapped[list[WorkflowRun]] = relationship(back_populates="workflow_version")


class WorkflowRun(Base):
    __tablename__ = "workflow_run"
    __table_args__ = (
        UniqueConstraint("temporal_wf_id", "temporal_run_id", name="uq_workflow_run_temporal_ids"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    temporal_wf_id: Mapped[str] = mapped_column(Text, nullable=False)
    temporal_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workflow_version.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # One of the AncoraRunStatus values (see catalog.py).
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # Phase 1 stores small payloads inline; object-store offload is Phase 4.
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    workflow_version: Mapped[WorkflowVersion] = relationship(back_populates="runs")


# --------------------------------------------------------------------------- #
# Execution runtime (Phase 2)
# --------------------------------------------------------------------------- #
class Worker(Base):
    """An activity worker's registration (AN-032).

    Rows are authoritative for *capabilities* (which pools/queues a worker serves)
    and *identity*; live health is a Redis TTL keyed by ``worker_id`` (a row can
    exist while the worker is dead — the registry marks it stale on read).
    """

    __tablename__ = "worker"

    id: Mapped[uuid.UUID] = _uuid_pk()
    worker_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Capability pools (["cpu","gpu"]) and the queues derived from them.
    pools: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    task_queues: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    # Advertised resources: total_cpus/total_gpus/accelerator_type.
    resources: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    registered_at: Mapped[datetime] = _created_at()
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Inbox(Base):
    """Idempotency inbox (AN-061): the exactly-once side-effect guard.

    A side-effecting node writes a row keyed by its ``idempotency_key`` before
    acting and stores the result after. On a retry or replay the guard finds the
    row and returns the stored result instead of re-issuing the effect — so a
    double-fired HTTP POST produces one request, not two. ``key`` is derived
    deterministically in the SDK (AN-062) from ``(workflow_id, node_id, input)``.
    """

    __tablename__ = "inbox"

    id: Mapped[uuid.UUID] = _uuid_pk()
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    temporal_wf_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # "pending" while the effect is in flight; "done" once the result is stored.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NodeExecution(Base):
    """Crude projection of a single activity/node dispatch (AN-028 support).

    Records where a node ran (backend + Ray task id) and its lifecycle. Like
    ``workflow_run`` this is a derived view; the event-sourced version arrives in
    Phase 4. Kept intentionally thin.
    """

    __tablename__ = "node_execution"

    id: Mapped[uuid.UUID] = _uuid_pk()
    temporal_wf_id: Mapped[str] = mapped_column(Text, nullable=False)
    node_name: Mapped[str] = mapped_column(String(255), nullable=False)
    capability: Mapped[str] = mapped_column(String(32), nullable=False)
    backend: Mapped[str] = mapped_column(String(32), nullable=False)  # "ray" | "local"
    ray_task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created_at()
