"""függő raktár-átadások (telephely↔autó jóváhagyással)

Revision ID: c8e3a7f5d219
Revises: b6d2f8e4a071
Create Date: 2026-08-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c8e3a7f5d219'
down_revision = 'b6d2f8e4a071'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "warehouse_transfers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "from_warehouse_id", sa.Uuid(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "to_warehouse_id", sa.Uuid(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "product_id", sa.Uuid(),
            sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_by", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_wh_transfers_creator"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "decided_by", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_wh_transfers_decider"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(length=512), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected','cancelled')",
            name="ck_wh_transfers_status",
        ),
    )
    op.create_index(
        "ix_wh_transfers_status", "warehouse_transfers", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_wh_transfers_status", table_name="warehouse_transfers")
    op.drop_table("warehouse_transfers")
