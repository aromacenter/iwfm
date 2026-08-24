"""worksheet customer note (ügyfél-példány megjegyzés)

Revision ID: 7c1d2e9a4b03
Revises: 686809310710
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '7c1d2e9a4b03'
down_revision = '686809310710'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worksheets", sa.Column("customer_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("worksheets", "customer_note")
