"""Eseti fizetési határidő az elszámoláson (settlements.due_days)

Revision ID: c4e6a8b0d115
Revises: a1c3e5b7f993
Create Date: 2026-09-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c4e6a8b0d115'
down_revision = 'a1c3e5b7f993'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settlements") as batch:
        batch.add_column(sa.Column("due_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("settlements") as batch:
        batch.drop_column("due_days")
