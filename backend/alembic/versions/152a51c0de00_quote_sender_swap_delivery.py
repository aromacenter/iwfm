"""Ajánlat kiküldője + gépcsere átadási módja (152. kör)

Revision ID: 152a51c0de00
Revises: 148a11c0de00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "152a51c0de00"
down_revision = "148a11c0de00"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("worksheets") as batch:
        batch.add_column(sa.Column("quote_sent_by", sa.Uuid(), nullable=True))
    with op.batch_alter_table("asset_protocols") as batch:
        batch.add_column(sa.Column("delivery_mode", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("delivery_ref", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("asset_protocols") as batch:
        batch.drop_column("delivery_ref")
        batch.drop_column("delivery_mode")
    with op.batch_alter_table("worksheets") as batch:
        batch.drop_column("quote_sent_by")
