"""Flotta-ból lenyomott csomag-katalógus a licencen

Revision ID: a7b9c1d3e551
Revises: f5c7e9a1b223
Create Date: 2026-08-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a7b9c1d3e551'
down_revision = 'f5c7e9a1b223'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "license_settings", sa.Column("plan_catalog", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("license_settings", "plan_catalog")
