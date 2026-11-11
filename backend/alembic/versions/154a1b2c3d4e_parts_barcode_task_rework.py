"""Alkatrész vonalkód + feladat visszatérő-javítás jelölés (154. kör)

Revision ID: 154a1b2c3d4e
Revises: 153a1c0de0a0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "154a1b2c3d4e"
down_revision = "153a1c0de0a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("parts") as batch:
        batch.add_column(sa.Column("barcode", sa.String(length=64), nullable=True))
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("rework", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("rework_of_task_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("rework_of_task_id")
        batch.drop_column("rework")
    with op.batch_alter_table("parts") as batch:
        batch.drop_column("barcode")
