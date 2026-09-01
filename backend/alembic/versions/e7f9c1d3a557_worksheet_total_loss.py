"""Gazdasági totálkár jelölés a munkalapon (worksheets.total_loss)

Revision ID: e7f9c1d3a557
Revises: d5f7b9c1e335
Create Date: 2026-09-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e7f9c1d3a557'
down_revision = 'd5f7b9c1e335'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("worksheets") as batch:
        batch.add_column(sa.Column(
            "total_loss", sa.Boolean(), nullable=False, server_default=sa.false()
        ))


def downgrade() -> None:
    with op.batch_alter_table("worksheets") as batch:
        batch.drop_column("total_loss")
