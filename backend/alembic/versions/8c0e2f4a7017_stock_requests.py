"""Áru-igénylés a központi raktárból (7bd7dbd8): a képviselő igényel, a
vezető a vezérlőpulton jóváhagyja → függő raktár-átadás jön létre, amit a
képviselő a saját raktáránál elfogad.

Revision ID: 8c0e2f4a7017
Revises: 7b9d1e3f6015
Create Date: 2026-10-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8c0e2f4a7017"
down_revision = "7b9d1e3f6015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stock_requests",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "user_id", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_streq_user"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id", sa.Uuid(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE", name="fk_streq_wh"),
            nullable=False,
        ),
        sa.Column(
            "product_id", sa.Uuid(),
            sa.ForeignKey("products.id", ondelete="CASCADE", name="fk_streq_product"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("requester_name", sa.String(length=256), nullable=True),
        sa.Column("decided_by_name", sa.String(length=256), nullable=True),
        sa.Column("transfer_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_stock_requests_status", "stock_requests", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_stock_requests_status", table_name="stock_requests")
    op.drop_table("stock_requests")
