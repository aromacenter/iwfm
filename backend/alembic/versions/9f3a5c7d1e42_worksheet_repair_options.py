"""worksheet repair options (javítási konstrukciók)

Revision ID: 9f3a5c7d1e42
Revises: 7c1d2e9a4b03
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '9f3a5c7d1e42'
down_revision = '7c1d2e9a4b03'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worksheets", sa.Column("repair_options", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("worksheets", "repair_options")
