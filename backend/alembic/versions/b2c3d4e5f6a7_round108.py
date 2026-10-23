"""108. kör (hibajegyek 2026-10-23): raktár láthatósági lista.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("warehouses") as b:
        b.add_column(sa.Column("allowed_user_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("warehouses") as b:
        b.drop_column("allowed_user_ids")
