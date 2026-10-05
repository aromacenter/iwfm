"""Sávos adagárazás (tiered modul) + leltár modul (stocktake).

- partner_contracts.price_tiers (JSON): [{"qty_from","qty_to","price"}] — a
  számlázott adagszám sávja adja az adagárat; partner-tükör:
  partners.contract_price_tiers (apply_active_contract szinkronizálja).
- stocktakes + stocktake_lines: raktári leltár-ívek; záráskor a számolt érték
  leltár-korrekciós (adjust) mozgással áll be.

Revision ID: 5f6b8d0e4011
Revises: 4e5a7c9d2009
Create Date: 2026-10-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5f6b8d0e4011"
down_revision = "4e5a7c9d2009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("partner_contracts", sa.Column("price_tiers", sa.JSON(), nullable=True))
    op.add_column("partners", sa.Column("contract_price_tiers", sa.JSON(), nullable=True))

    op.create_table(
        "stocktakes",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "warehouse_id", sa.Uuid(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE", name="fk_stocktakes_wh"),
            nullable=False,
        ),
        sa.Column("warehouse_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("opened_by_name", sa.String(length=256), nullable=True),
        sa.Column("closed_by_name", sa.String(length=256), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_stocktakes_wh", "stocktakes", ["warehouse_id", "opened_at"])

    op.create_table(
        "stocktake_lines",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "stocktake_id", sa.Uuid(),
            sa.ForeignKey("stocktakes.id", ondelete="CASCADE", name="fk_stlines_st"),
            nullable=False,
        ),
        sa.Column(
            "product_id", sa.Uuid(),
            sa.ForeignKey("products.id", ondelete="CASCADE", name="fk_stlines_product"),
            nullable=False,
        ),
        sa.Column("product_name", sa.String(length=256), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False, server_default="db"),
        sa.Column("system_qty", sa.Float(), nullable=False, server_default="0"),
        sa.Column("counted_qty", sa.Float(), nullable=True),
    )
    op.create_index("ix_stocktake_lines_st", "stocktake_lines", ["stocktake_id"])


def downgrade() -> None:
    op.drop_index("ix_stocktake_lines_st", table_name="stocktake_lines")
    op.drop_table("stocktake_lines")
    op.drop_index("ix_stocktakes_wh", table_name="stocktakes")
    op.drop_table("stocktakes")
    op.drop_column("partners", "contract_price_tiers")
    op.drop_column("partner_contracts", "price_tiers")
