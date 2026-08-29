"""Több futár (MPL/FoxPost/DPD): courier_settings + carrier a csomagokon

Revision ID: e1a3c5d7f997
Revises: d7f9a1b3c885
Create Date: 2026-08-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e1a3c5d7f997'
down_revision = 'd7f9a1b3c885'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "courier_settings",
        sa.Column("carrier", sa.String(length=16), primary_key=True),
        sa.Column("config_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "gls_parcels",
        sa.Column("carrier", sa.String(length=16), nullable=False, server_default="gls"),
    )
    op.add_column(
        "gls_parcels", sa.Column("carrier_ref", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("gls_parcels", "carrier_ref")
    op.drop_column("gls_parcels", "carrier")
    op.drop_table("courier_settings")
