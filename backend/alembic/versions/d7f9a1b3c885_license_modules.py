"""Modul-kapcsolók a licencen (extra funkciók példányonként)

Revision ID: d7f9a1b3c885
Revises: c5d7e9f1a663
Create Date: 2026-08-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd7f9a1b3c885'
down_revision = 'c5d7e9f1a663'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "license_settings",
        sa.Column("enabled_modules", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("license_settings", "enabled_modules")
