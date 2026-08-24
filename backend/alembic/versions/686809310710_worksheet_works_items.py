"""worksheet works items (tételes munkadíjak)

Revision ID: 686809310710
Revises: 079c3bb11f74
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '686809310710'
down_revision = '079c3bb11f74'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worksheets", sa.Column("works", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("worksheets", "works")
