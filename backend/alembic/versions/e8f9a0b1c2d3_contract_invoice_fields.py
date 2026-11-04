"""Szerződés: számla-referencia + havi bérleti díjas számlasor (144. kör)

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.add_column(sa.Column("invoice_reference", sa.String(length=256), nullable=True))
        batch.add_column(sa.Column("invoice_monthly_rent", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.drop_column("invoice_monthly_rent")
        batch.drop_column("invoice_reference")
