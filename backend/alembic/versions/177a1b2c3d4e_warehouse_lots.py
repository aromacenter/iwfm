"""Tételek lejárattal + FIFO (177. kör): warehouse_lots, products.shelf_life_days,
notification_settings.last_expiry_notice

Revision ID: 177a1b2c3d4e
Revises: 174a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "177a1b2c3d4e"
down_revision = "174a1b2c3d4e"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("warehouse_lots"):
        op.create_table(
            "warehouse_lots",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False),
            sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
            sa.Column("location_id", sa.Uuid(), sa.ForeignKey("warehouse_locations.id", ondelete="SET NULL"), nullable=True),
            sa.Column("expires_at", sa.Date(), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=False, server_default="0"),
            sa.Column("note", sa.String(256), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_warehouse_lots_wh_product", "warehouse_lots", ["warehouse_id", "product_id"])
        op.create_index("ix_warehouse_lots_expires", "warehouse_lots", ["expires_at"])
    if "shelf_life_days" not in _cols("products"):
        with op.batch_alter_table("products") as batch:
            batch.add_column(sa.Column("shelf_life_days", sa.Integer(), nullable=True))
    if "last_expiry_notice" not in _cols("notification_settings"):
        with op.batch_alter_table("notification_settings") as batch:
            batch.add_column(sa.Column("last_expiry_notice", sa.String(10), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "last_expiry_notice" in _cols("notification_settings"):
        with op.batch_alter_table("notification_settings") as batch:
            batch.drop_column("last_expiry_notice")
    if "shelf_life_days" in _cols("products"):
        with op.batch_alter_table("products") as batch:
            batch.drop_column("shelf_life_days")
    if insp.has_table("warehouse_lots"):
        op.drop_table("warehouse_lots")
