"""112. kör (hibajegyek 2026-10-27): elszámolás kerekítési különbözete +
számlázói bizonylatszám.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settlements") as b:
        b.add_column(sa.Column("billingo_document_number", sa.String(64), nullable=True))
        b.add_column(sa.Column("rounding_adjust", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("settlements") as b:
        b.drop_column("rounding_adjust")
        b.drop_column("billingo_document_number")
