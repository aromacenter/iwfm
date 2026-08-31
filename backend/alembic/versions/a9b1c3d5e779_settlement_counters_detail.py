"""Elszámolás számlálónkénti bontása (settlement_machines.counters_detail)

Revision ID: a9b1c3d5e779
Revises: f7d9e1b3a557
Create Date: 2026-08-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a9b1c3d5e779'
down_revision = 'f7d9e1b3a557'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settlement_machines") as batch:
        batch.add_column(sa.Column("counters_detail", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("settlement_machines") as batch:
        batch.drop_column("counters_detail")
