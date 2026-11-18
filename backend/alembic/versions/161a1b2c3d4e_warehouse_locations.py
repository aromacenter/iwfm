"""Tárhelyezett raktár: sor/állás/szint/fakk tárhelyek + készlet-bontás (161. kör)

Revision ID: 161a1b2c3d4e
Revises: 160a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "161a1b2c3d4e"
down_revision = "160a1b2c3d4e"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    have = _cols("warehouses")
    with op.batch_alter_table("warehouses") as batch:
        if "layout" not in have:
            batch.add_column(sa.Column("layout", sa.String(8), nullable=False, server_default="simple"))
        if "row_scheme" not in have:
            batch.add_column(sa.Column("row_scheme", sa.String(8), nullable=False, server_default="letters"))
    have = _cols("warehouse_movements")
    with op.batch_alter_table("warehouse_movements") as batch:
        if "location_id" not in have:
            batch.add_column(sa.Column("location_id", sa.Uuid(), nullable=True))
        if "ref_location_id" not in have:
            batch.add_column(sa.Column("ref_location_id", sa.Uuid(), nullable=True))
    tables = _tables()
    if "warehouse_locations" not in tables:
        op.create_table(
            "warehouse_locations",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False),
            sa.Column("row_code", sa.String(4), nullable=False),
            sa.Column("bay", sa.Integer(), nullable=False),
            sa.Column("level", sa.Integer(), nullable=False),
            sa.Column("slot", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("code", sa.String(24), nullable=False),
            sa.Column("label", sa.String(64), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("warehouse_id", "row_code", "bay", "level", "slot", name="uq_warehouse_locations_pos"),
            sa.UniqueConstraint("warehouse_id", "code", name="uq_warehouse_locations_code"),
        )
        op.create_index("ix_warehouse_locations_wh", "warehouse_locations", ["warehouse_id"])
    if "warehouse_stock_locations" not in tables:
        op.create_table(
            "warehouse_stock_locations",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False),
            sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
            sa.Column("location_id", sa.Uuid(), sa.ForeignKey("warehouse_locations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("quantity", sa.Float(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("warehouse_id", "product_id", "location_id", name="uq_warehouse_stock_locations"),
        )
        op.create_index("ix_warehouse_stock_locations_wh", "warehouse_stock_locations", ["warehouse_id", "product_id"])


def downgrade() -> None:
    op.drop_index("ix_warehouse_stock_locations_wh", table_name="warehouse_stock_locations")
    op.drop_table("warehouse_stock_locations")
    op.drop_index("ix_warehouse_locations_wh", table_name="warehouse_locations")
    op.drop_table("warehouse_locations")
    with op.batch_alter_table("warehouse_movements") as batch:
        batch.drop_column("ref_location_id")
        batch.drop_column("location_id")
    with op.batch_alter_table("warehouses") as batch:
        batch.drop_column("row_scheme")
        batch.drop_column("layout")
