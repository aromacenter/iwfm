"""címkenyomtatási sor + nyomtató-ügynök beállítások (Godex)

Revision ID: b6d2f8e4a071
Revises: a3c9e1f5b627
Create Date: 2026-08-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b6d2f8e4a071'
down_revision = 'a3c9e1f5b627'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "print_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_by", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_print_jobs_user"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_print_jobs_status", "print_jobs", ["status", "created_at"])
    op.create_table(
        "print_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_key", sa.String(length=128), nullable=True),
        sa.Column("agent_last_seen", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("print_settings")
    op.drop_index("ix_print_jobs_status", table_name="print_jobs")
    op.drop_table("print_jobs")
