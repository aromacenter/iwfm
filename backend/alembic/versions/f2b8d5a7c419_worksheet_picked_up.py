"""worksheet picked up (gép elhozva a szerelőtől)

Revision ID: f2b8d5a7c419
Revises: e9a2c6d4b183
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f2b8d5a7c419'
down_revision = 'e9a2c6d4b183'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worksheets", sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("worksheets", "picked_up_at")
