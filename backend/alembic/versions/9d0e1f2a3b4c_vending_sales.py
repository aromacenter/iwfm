"""Kávégép napi fogyás (ügynök 3.5+): melyik italból mennyi, fizetési mód,
sikeres főzések és halmozott számláló naponként — a gép rendelésnaplójából.

Revision ID: 9d0e1f2a3b4c
Revises: 8c9d1e2f3a4b
Create Date: 2026-10-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9d0e1f2a3b4c"
down_revision = "8c9d1e2f3a4b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ping / parancsok / hely a gép-soron
    with op.batch_alter_table("vending_machines") as b:
        b.add_column(sa.Column("pending_commands", sa.JSON(), nullable=True))
        b.add_column(sa.Column("ip", sa.String(64), nullable=True))
        b.add_column(sa.Column("geo", sa.JSON(), nullable=True))
    op.create_table(
        "vending_sales_days",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("machine_id", sa.Uuid(), sa.ForeignKey("vending_machines.id", ondelete="CASCADE", name="fk_vsd_machine"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("drinks_ok", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pay", sa.JSON(), nullable=True),
        sa.Column("counter_total", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("machine_id", "day", name="uq_vsd_machine_day"),
    )
    op.create_table(
        "vending_sales_products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("machine_id", sa.Uuid(), sa.ForeignKey("vending_machines.id", ondelete="CASCADE", name="fk_vsp_machine"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("name_key", sa.String(64), nullable=False, server_default=""),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("machine_id", "day", "product_id", name="uq_vsp_machine_day_product"),
    )
    op.create_index("ix_vsp_machine_day", "vending_sales_products", ["machine_id", "day"])


def downgrade() -> None:
    op.drop_index("ix_vsp_machine_day", table_name="vending_sales_products")
    op.drop_table("vending_sales_products")
    op.drop_table("vending_sales_days")
    with op.batch_alter_table("vending_machines") as b:
        for col in ("geo", "ip", "pending_commands"):
            b.drop_column(col)
