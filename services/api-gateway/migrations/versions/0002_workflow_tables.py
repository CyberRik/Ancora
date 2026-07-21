"""workflow catalog + run projection; seed default org/project

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-21

Phase 1 (AN-019): the workflow catalog (def/version) and the run projection.
Also seeds the single-tenant default org/project used until multi-tenancy (Phase 6).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_PROJECT_ID = "00000000-0000-0000-0000-000000000002"


def upgrade() -> None:
    op.create_table(
        "workflow_def",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["project.id"], ondelete="CASCADE", name="fk_wfdef_project"
        ),
        sa.UniqueConstraint("project_id", "name", name="uq_workflow_def_project_name"),
    )
    op.create_index("ix_workflow_def_project_id", "workflow_def", ["project_id"])

    op.create_table(
        "workflow_version",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_def_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("dag_spec", JSONB(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("determinism_token", sa.Text(), nullable=False),
        sa.Column("task_queue", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_def_id"],
            ["workflow_def.id"],
            ondelete="CASCADE",
            name="fk_wfversion_def",
        ),
        sa.UniqueConstraint("workflow_def_id", "version", name="uq_workflow_version_def_version"),
    )
    op.create_index("ix_workflow_version_def_id", "workflow_version", ["workflow_def_id"])

    op.create_table(
        "workflow_run",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("temporal_wf_id", sa.Text(), nullable=False),
        sa.Column("temporal_run_id", sa.Text(), nullable=False),
        sa.Column("workflow_version_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input", JSONB(), nullable=True),
        sa.Column("output", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_version_id"],
            ["workflow_version.id"],
            ondelete="RESTRICT",
            name="fk_wfrun_version",
        ),
        sa.UniqueConstraint(
            "temporal_wf_id", "temporal_run_id", name="uq_workflow_run_temporal_ids"
        ),
    )
    op.create_index("ix_workflow_run_status", "workflow_run", ["status"])
    op.create_index("ix_workflow_run_version_id", "workflow_run", ["workflow_version_id"])

    # Seed the single-tenant defaults (idempotent via fixed UUIDs).
    op.execute(
        sa.text(
            "INSERT INTO org (id, name) VALUES (:id, 'default') ON CONFLICT (id) DO NOTHING"
        ).bindparams(id=DEFAULT_ORG_ID)
    )
    op.execute(
        sa.text(
            "INSERT INTO project (id, org_id, name) VALUES (:id, :org, 'default') "
            "ON CONFLICT (id) DO NOTHING"
        ).bindparams(id=DEFAULT_PROJECT_ID, org=DEFAULT_ORG_ID)
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_run_version_id", table_name="workflow_run")
    op.drop_index("ix_workflow_run_status", table_name="workflow_run")
    op.drop_table("workflow_run")
    op.drop_index("ix_workflow_version_def_id", table_name="workflow_version")
    op.drop_table("workflow_version")
    op.drop_index("ix_workflow_def_project_id", table_name="workflow_def")
    op.drop_table("workflow_def")
    op.execute(sa.text("DELETE FROM project WHERE id = :id").bindparams(id=DEFAULT_PROJECT_ID))
    op.execute(sa.text("DELETE FROM org WHERE id = :id").bindparams(id=DEFAULT_ORG_ID))
