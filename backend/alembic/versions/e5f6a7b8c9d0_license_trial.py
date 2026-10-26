"""Onboarding (2026-10-26): próbaidő-jelző + kért számlázási ciklus a licencen.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("license_settings") as b:
        b.add_column(sa.Column("is_trial", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("requested_cycle", sa.String(8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("license_settings") as b:
        b.drop_column("requested_cycle")
        b.drop_column("is_trial")
