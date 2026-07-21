"""base tables: org, project, user

Revision ID: 0001
Revises:
Create Date: 2026-07-21

Phase 0 (AN-006): the tenancy roots that later phases hang off of.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_org_name"),
    )

    op.create_table(
        "project",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["org.id"], ondelete="CASCADE", name="fk_project_org"),
        sa.UniqueConstraint("org_id", "name", name="uq_project_org_name"),
    )
    op.create_index("ix_project_org_id", "project", ["org_id"])

    op.create_table(
        "user",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["org.id"], ondelete="CASCADE", name="fk_user_org"),
        sa.UniqueConstraint("org_id", "email", name="uq_user_org_email"),
    )
    op.create_index("ix_user_org_id", "user", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_user_org_id", table_name="user")
    op.drop_table("user")
    op.drop_index("ix_project_org_id", table_name="project")
    op.drop_table("project")
    op.drop_table("org")
