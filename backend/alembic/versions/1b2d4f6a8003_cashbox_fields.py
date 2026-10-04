"""Kassza-mezők a költségeken (entry_type, beszállító, bizonylatszám)

Revision ID: 1b2d4f6a8003
Revises: 0a1c3e5b7991
Create Date: 2026-10-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '1b2d4f6a8003'
down_revision = '0a1c3e5b7991'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_expenses") as batch:
        batch.add_column(sa.Column(
            "entry_type", sa.String(16), nullable=False, server_default="expense",
        ))
        batch.add_column(sa.Column("supplier", sa.String(256), nullable=True))
        batch.add_column(sa.Column("receipt_no", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_expenses") as batch:
        batch.drop_column("receipt_no")
        batch.drop_column("supplier")
        batch.drop_column("entry_type")
