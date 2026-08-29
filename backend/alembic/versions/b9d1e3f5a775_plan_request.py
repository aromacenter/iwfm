"""Csomagváltás-kérés mezők a licencen

Revision ID: b9d1e3f5a775
Revises: a7b9c1d3e551
Create Date: 2026-08-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b9d1e3f5a775'
down_revision = 'a7b9c1d3e551'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "license_settings", sa.Column("requested_plan", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "license_settings",
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "license_settings", sa.Column("requested_by", sa.String(length=128), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("license_settings", "requested_by")
    op.drop_column("license_settings", "requested_at")
    op.drop_column("license_settings", "requested_plan")
