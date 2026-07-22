"""idempotency inbox (exactly-once side-effect guard)

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-23

Phase 3 (AN-061): the ``inbox`` table backs the exactly-once guarantee for
side-effecting nodes. A row keyed by the derived ``idempotency_key`` records the
result of a logical call; retries/replays return the stored result instead of
re-issuing the effect.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inbox",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("temporal_wf_id", sa.Text(), nullable=True),
        sa.Column("node_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key", name="uq_inbox_key"),
    )


def downgrade() -> None:
    op.drop_table("inbox")
