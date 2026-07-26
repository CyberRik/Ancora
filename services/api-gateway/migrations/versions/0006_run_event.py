"""event-sourced run-event log

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-26

Phase 4 (RFC-0001 §8). The durable landing table for the live event stream: the
event consumer drains Redis Streams into ``run_event`` so the timeline survives a
Redis restart and can be replayed after the fact. Append-only; ``stream_id`` (the
global Redis entry id, globally unique and monotonic) is the idempotency key that
makes at-least-once redelivery a no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("stream_id", sa.String(64), nullable=False),
        sa.Column("temporal_wf_id", sa.Text(), nullable=False),
        sa.Column("temporal_run_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("node_id", sa.String(255), nullable=True),
        sa.Column("activity_id", sa.String(64), nullable=True),
        sa.Column("activity_type", sa.String(255), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("event_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # The Redis entry id dedupes at-least-once redelivery.
        sa.UniqueConstraint("stream_id", name="uq_run_event_stream_id"),
    )
    # The UI reads a run's timeline in order: index (wf_id, id).
    op.create_index("ix_run_event_wf_id_id", "run_event", ["temporal_wf_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_run_event_wf_id_id", table_name="run_event")
    op.drop_table("run_event")
