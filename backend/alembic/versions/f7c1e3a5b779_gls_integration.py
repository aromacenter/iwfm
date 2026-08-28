"""GLS (MyGLS) integráció: beállítások + feladott csomagok

Revision ID: f7c1e3a5b779
Revises: e5a7c9d1b333
Create Date: 2026-08-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f7c1e3a5b779'
down_revision = 'e5a7c9d1b333'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gls_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=320), nullable=True),
        sa.Column("password_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("client_number", sa.String(length=32), nullable=True),
        sa.Column("test_mode", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "printer_type", sa.String(length=16), nullable=False, server_default="A4_2x2"
        ),
        sa.Column("sender_name", sa.String(length=256), nullable=True),
        sa.Column("sender_zip", sa.String(length=16), nullable=True),
        sa.Column("sender_city", sa.String(length=128), nullable=True),
        sa.Column("sender_street", sa.String(length=256), nullable=True),
        sa.Column("sender_house", sa.String(length=32), nullable=True),
        sa.Column("sender_phone", sa.String(length=32), nullable=True),
        sa.Column("sender_email", sa.String(length=320), nullable=True),
    )
    op.create_table(
        "gls_parcels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "order_id", sa.Uuid(),
            sa.ForeignKey("product_orders.id", ondelete="SET NULL", name="fk_gls_order"),
            nullable=True,
        ),
        sa.Column(
            "partner_id", sa.Uuid(),
            sa.ForeignKey("partners.id", ondelete="SET NULL", name="fk_gls_partner"),
            nullable=True,
        ),
        sa.Column("recipient_name", sa.String(length=256), nullable=False),
        sa.Column("recipient_zip", sa.String(length=16), nullable=False),
        sa.Column("recipient_city", sa.String(length=128), nullable=False),
        sa.Column("recipient_street", sa.String(length=256), nullable=False),
        sa.Column("recipient_house", sa.String(length=32), nullable=True),
        sa.Column("recipient_phone", sa.String(length=32), nullable=True),
        sa.Column("recipient_email", sa.String(length=320), nullable=True),
        sa.Column("content", sa.String(length=256), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cod_amount", sa.Float(), nullable=True),
        sa.Column("cod_reference", sa.String(length=64), nullable=True),
        sa.Column("parcel_number", sa.String(length=32), nullable=True),
        sa.Column("gls_parcel_id", sa.BigInteger(), nullable=True),
        sa.Column("label_pdf", sa.LargeBinary(), nullable=True),
        sa.Column(
            "status_key", sa.String(length=24), nullable=False, server_default="created"
        ),
        sa.Column("last_status", sa.String(length=256), nullable=True),
        sa.Column("last_status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_history", sa.JSON(), nullable=True),
        sa.Column("test_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_by", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_gls_user"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_gls_parcels_created", "gls_parcels", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_gls_parcels_created", table_name="gls_parcels")
    op.drop_table("gls_parcels")
    op.drop_table("gls_settings")
