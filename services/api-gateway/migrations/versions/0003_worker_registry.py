"""execution runtime: worker registry + node_execution projection

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-22

Phase 2 (AN-032): the worker registry (authoritative capabilities; live health
is a Redis TTL) and the crude ``node_execution`` projection (AN-028 support).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("worker_id", sa.String(255), nullable=False),
        sa.Column("host", sa.String(255), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("pools", JSONB(), nullable=False),
        sa.Column("task_queues", JSONB(), nullable=False),
        sa.Column("resources", JSONB(), nullable=False),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("worker_id", name="uq_worker_worker_id"),
    )

    op.create_table(
        "node_execution",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("temporal_wf_id", sa.Text(), nullable=False),
        sa.Column("node_name", sa.String(255), nullable=False),
        sa.Column("capability", sa.String(32), nullable=False),
        sa.Column("backend", sa.String(32), nullable=False),
        sa.Column("ray_task_id", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_node_execution_wf_id", "node_execution", ["temporal_wf_id"])
    op.create_index("ix_node_execution_status", "node_execution", ["status"])


def downgrade() -> None:
    op.drop_index("ix_node_execution_status", table_name="node_execution")
    op.drop_index("ix_node_execution_wf_id", table_name="node_execution")
    op.drop_table("node_execution")
    op.drop_table("worker")
