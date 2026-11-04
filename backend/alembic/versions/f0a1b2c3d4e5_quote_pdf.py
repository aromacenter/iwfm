"""Hivatalos árajánlat: ajánlat-sorszám + PO a munkalapon, aláírás/bélyegző a beállításokban (145. kör)

Revision ID: f0a1b2c3d4e5
Revises: e8f9a0b1c2d3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f0a1b2c3d4e5"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("worksheets") as batch:
        batch.add_column(sa.Column("quote_serial", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("po_number", sa.String(length=64), nullable=True))
    with op.batch_alter_table("worksheet_settings") as batch:
        batch.add_column(sa.Column("signature_data", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("signature_mime", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("stamp_data", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("stamp_mime", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("signer_name", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("signer_title", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("worksheet_settings") as batch:
        for col in ("signer_title", "signer_name", "stamp_mime", "stamp_data", "signature_mime", "signature_data"):
            batch.drop_column(col)
    with op.batch_alter_table("worksheets") as batch:
        batch.drop_column("po_number")
        batch.drop_column("quote_serial")
