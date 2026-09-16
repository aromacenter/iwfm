"""Szabadszavas számláló-nevek a gépeken (assets.counter_names)

Revision ID: e8a0c2d4f339
Revises: c4e6a8b0d115
Create Date: 2026-09-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e8a0c2d4f339'
down_revision = 'c4e6a8b0d115'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.add_column(sa.Column("counter_names", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.drop_column("counter_names")
