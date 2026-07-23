"""cost ledger, approval-gate index, retry-attempt log

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-23

Phase 3 projections (AN-057, AN-064, AN-044). All three are *derived* views over
Temporal history — the authoritative record of what a run cost, what a human
decided, and why a node retried lives in the workflow's event history. These
tables exist so the UI and the cost/approval APIs can answer questions without
replaying every run.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cost_ledger",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("temporal_wf_id", sa.Text(), nullable=False),
        sa.Column("node_id", sa.String(255), nullable=False),
        sa.Column("node_type", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("provider", sa.String(128), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("usd", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gpu_seconds", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # One row per attempt that actually ran: a replay re-derives the cost from
        # history and must not double-count it.
        sa.UniqueConstraint(
            "temporal_wf_id", "node_id", "attempt", name="uq_cost_ledger_node_attempt"
        ),
    )
    op.create_index("ix_cost_ledger_temporal_wf_id", "cost_ledger", ["temporal_wf_id"])

    op.create_table(
        "approval_gate",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("temporal_wf_id", sa.Text(), nullable=False),
        sa.Column("gate_id", sa.String(255), nullable=False),
        sa.Column("workflow_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="waiting"),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(320), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.UniqueConstraint("temporal_wf_id", "gate_id", name="uq_approval_gate_wf_gate"),
    )
    op.create_index("ix_approval_gate_temporal_wf_id", "approval_gate", ["temporal_wf_id"])
    # The inbox query is "everything still waiting", so index for it directly.
    op.create_index("ix_approval_gate_status", "approval_gate", ["status", "requested_at"])

    op.create_table(
        "retry_attempt",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("temporal_wf_id", sa.Text(), nullable=False),
        sa.Column("node_id", sa.String(255), nullable=False),
        sa.Column("node_type", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("transient", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retry_after_seconds", sa.Numeric(12, 3), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_retry_attempt_temporal_wf_id", "retry_attempt", ["temporal_wf_id"])


def downgrade() -> None:
    op.drop_index("ix_retry_attempt_temporal_wf_id", table_name="retry_attempt")
    op.drop_table("retry_attempt")
    op.drop_index("ix_approval_gate_status", table_name="approval_gate")
    op.drop_index("ix_approval_gate_temporal_wf_id", table_name="approval_gate")
    op.drop_table("approval_gate")
    op.drop_index("ix_cost_ledger_temporal_wf_id", table_name="cost_ledger")
    op.drop_table("cost_ledger")
