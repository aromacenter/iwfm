"""Levélküldés-napló (158. kör)

Revision ID: 158a1b2c3d4e
Revises: 157a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "158a1b2c3d4e"
down_revision = "157a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_campaigns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("to_address", sa.String(length=320), nullable=True),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_by_name", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mail_campaigns_created", "mail_campaigns", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mail_campaigns_created", table_name="mail_campaigns")
    op.drop_table("mail_campaigns")
