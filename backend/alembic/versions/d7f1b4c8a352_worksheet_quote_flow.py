"""worksheet quote flow (ügyfél-árajánlat linkkel)

Revision ID: d7f1b4c8a352
Revises: c5e7a9b3f261
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd7f1b4c8a352'
down_revision = 'c5e7a9b3f261'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worksheets", sa.Column("quote_token", sa.String(length=64), nullable=True))
    op.add_column(
        "worksheets",
        sa.Column("quote_status", sa.String(length=16), nullable=False, server_default="none"),
    )
    op.add_column("worksheets", sa.Column("quote_email", sa.String(length=320), nullable=True))
    op.add_column("worksheets", sa.Column("quote_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("worksheets", sa.Column("quote_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("worksheets", sa.Column("quote_selected_name", sa.String(length=256), nullable=True))
    op.add_column("worksheets", sa.Column("quote_accepted_by", sa.String(length=256), nullable=True))


def downgrade() -> None:
    for col in (
        "quote_accepted_by", "quote_selected_name", "quote_accepted_at",
        "quote_sent_at", "quote_email", "quote_status", "quote_token",
    ):
        op.drop_column("worksheets", col)
