"""Szerződéses adagár számlálónként (assets.counter_prices)

Revision ID: f7d9e1b3a557
Revises: e5b7c9d1f331
Create Date: 2026-08-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f7d9e1b3a557'
down_revision = 'e5b7c9d1f331'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.add_column(sa.Column("counter_prices", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.drop_column("counter_prices")
