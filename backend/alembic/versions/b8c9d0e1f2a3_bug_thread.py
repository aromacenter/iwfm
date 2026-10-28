"""114. kör: párbeszéd-szál a hibajegyen (bug_comments) + olvasottsági
időbélyegek a bug_reports táblán.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bug_reports") as b:
        b.add_column(sa.Column("reporter_seen_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("operator_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "bug_comments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("bug_id", sa.Uuid(), sa.ForeignKey("bug_reports.id", ondelete="CASCADE", name="fk_bug_comment_bug"), nullable=False),
        sa.Column("author_kind", sa.String(16), nullable=False),
        sa.Column("author_name", sa.String(128), nullable=False, server_default="?"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bug_comments_bug", "bug_comments", ["bug_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_bug_comments_bug", table_name="bug_comments")
    op.drop_table("bug_comments")
    with op.batch_alter_table("bug_reports") as b:
        b.drop_column("operator_seen_at")
        b.drop_column("reporter_seen_at")
