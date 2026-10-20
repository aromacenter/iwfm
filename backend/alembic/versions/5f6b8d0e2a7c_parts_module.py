"""Alkatrész-raktár (parts modul, 1d553d2e, A fázis): cikktörzs, helykódos
raktárak (oszlop × sor), készlet és mozgásnapló — a termék-raktártól független.

Revision ID: 5f6b8d0e2a7c
Revises: 4e5a7c9d1f6b
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5f6b8d0e2a7c"
down_revision = "4e5a7c9d1f6b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parts_warehouses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False, server_default="central"),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_parts_wh_owner"), nullable=True),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('central','vehicle')", name="ck_parts_wh_kind"),
    )
    op.create_index("ix_parts_warehouses_name", "parts_warehouses", ["name"])
    op.create_table(
        "parts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("manufacturer_code", sa.String(128), nullable=True),
        sa.Column("unit", sa.String(16), nullable=False, server_default="db"),
        sa.Column("min_stock", sa.Float(), nullable=False, server_default="0"),
        sa.Column("price_net", sa.Float(), nullable=True),
        sa.Column("cost_net", sa.Float(), nullable=True),
        sa.Column("supplier", sa.String(256), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("sku", name="uq_parts_sku"),
    )
    op.create_index("ix_parts_name", "parts", ["name"])
    op.create_table(
        "parts_stock",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("parts_warehouses.id", ondelete="CASCADE", name="fk_parts_stock_wh"), nullable=False),
        sa.Column("part_id", sa.Uuid(), sa.ForeignKey("parts.id", ondelete="CASCADE", name="fk_parts_stock_part"), nullable=False),
        sa.Column("location", sa.String(16), nullable=False, server_default=""),
        sa.Column("quantity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("warehouse_id", "part_id", "location", name="uq_parts_stock"),
    )
    op.create_index("ix_parts_stock_wh", "parts_stock", ["warehouse_id"])
    op.create_table(
        "parts_movements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("part_id", sa.Uuid(), sa.ForeignKey("parts.id", ondelete="CASCADE", name="fk_parts_mv_part"), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("parts_warehouses.id", ondelete="CASCADE", name="fk_parts_mv_wh"), nullable=False),
        sa.Column("location", sa.String(16), nullable=False, server_default=""),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("quantity_delta", sa.Float(), nullable=False),
        sa.Column("unit_cost", sa.Float(), nullable=True),
        sa.Column("ref_warehouse_id", sa.Uuid(), nullable=True),
        sa.Column("ref_location", sa.String(16), nullable=True),
        sa.Column("worksheet_id", sa.Uuid(), nullable=True),
        sa.Column("document_no", sa.String(64), nullable=True),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('receipt','issue','use','transfer','adjust')", name="ck_parts_mv_kind"),
    )
    op.create_index("ix_parts_movements_wh", "parts_movements", ["warehouse_id", "created_at"])
    op.create_index("ix_parts_movements_part", "parts_movements", ["part_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_parts_movements_part", table_name="parts_movements")
    op.drop_index("ix_parts_movements_wh", table_name="parts_movements")
    op.drop_table("parts_movements")
    op.drop_index("ix_parts_stock_wh", table_name="parts_stock")
    op.drop_table("parts_stock")
    op.drop_index("ix_parts_name", table_name="parts")
    op.drop_table("parts")
    op.drop_index("ix_parts_warehouses_name", table_name="parts_warehouses")
    op.drop_table("parts_warehouses")
