"""Automata számlázás kapcsoló a szerződésen (partner_contracts.auto_billing)

Revision ID: f0b2d4e6a551
Revises: e8a0c2d4f339
Create Date: 2026-09-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f0b2d4e6a551'
down_revision = 'e8a0c2d4f339'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.add_column(sa.Column(
            "auto_billing", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))


def downgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.drop_column("auto_billing")
