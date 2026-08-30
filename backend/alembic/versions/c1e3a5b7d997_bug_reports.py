"""Hibabejelentő modul (bug_reports)

Revision ID: c1e3a5b7d997
Revises: b9d1e3f5a775
Create Date: 2026-08-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c1e3a5b7d997'
down_revision = 'b9d1e3f5a775'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bug_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("page_url", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="minor"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="new"),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.Column("screenshot", sa.LargeBinary(), nullable=True),
        sa.Column("screenshot_mime", sa.String(length=32), nullable=True),
        sa.Column(
            "reporter_id", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_bug_reporter"),
            nullable=True,
        ),
        sa.Column("reporter_name", sa.String(length=128), nullable=False, server_default="?"),
        sa.Column("fix_group", sa.String(length=64), nullable=True),
        sa.Column("resolution_note", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_bugs_status", "bug_reports", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_bugs_status", table_name="bug_reports")
    op.drop_table("bug_reports")
