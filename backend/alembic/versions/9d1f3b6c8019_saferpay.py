"""Saferpay QR-fizetés a kávégépekhez: beállítás-sor (hitelesítő adatok,
Fernet-titkosított jelszóval) + payment_orders (QR-rendelések életútja).
A funkció a "saferpay" licenc-modullal kapcsolható a Flotta-pultból.

Revision ID: 9d1f3b6c8019
Revises: 8c0e2f4a7017
Create Date: 2026-10-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9d1f3b6c8019"
down_revision = "8c0e2f4a7017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saferpay_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("customer_id", sa.String(length=32), nullable=True),
        sa.Column("terminal_id", sa.String(length=32), nullable=True),
        sa.Column("api_username", sa.String(length=128), nullable=True),
        sa.Column("api_password_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("test_mode", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="HUF"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "payment_products",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("price_huf", sa.Float(), nullable=False),
        sa.Column("machine_code", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "payment_orders",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("order_no", sa.String(length=32), nullable=False),
        sa.Column("machine_code", sa.String(length=64), nullable=True),
        sa.Column(
            "asset_id", sa.Uuid(),
            sa.ForeignKey("assets.id", ondelete="SET NULL", name="fk_payorder_asset"),
            nullable=True,
        ),
        sa.Column("product_name", sa.String(length=256), nullable=False),
        sa.Column("amount_huf", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="created"),
        sa.Column("token", sa.String(length=128), nullable=True),
        sa.Column("redirect_url", sa.String(length=1024), nullable=True),
        sa.Column("transaction_id", sa.String(length=64), nullable=True),
        sa.Column("payment_means", sa.String(length=64), nullable=True),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column(
            "created_by", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_payorder_user"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_payment_orders_status", "payment_orders", ["status", "created_at"])
    op.create_index("ix_payment_orders_machine", "payment_orders", ["machine_code", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_payment_orders_machine", table_name="payment_orders")
    op.drop_index("ix_payment_orders_status", table_name="payment_orders")
    op.drop_table("payment_orders")
    op.drop_table("payment_products")
    op.drop_table("saferpay_settings")
