"""Termék cikkszám (products.code)

Revision ID: f9b1d3e5c779
Revises: e7f9c1d3a557
Create Date: 2026-09-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f9b1d3e5c779'
down_revision = 'e7f9c1d3a557'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("code", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.drop_column("code")
