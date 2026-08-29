"""Termék-árváltozás napló (product_price_logs)

Revision ID: f5c7e9a1b223
Revises: e1a3c5d7f997
Create Date: 2026-08-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f5c7e9a1b223'
down_revision = 'e1a3c5d7f997'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_price_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "product_id", sa.Uuid(),
            sa.ForeignKey("products.id", ondelete="CASCADE", name="fk_pricelog_product"),
            nullable=False,
        ),
        sa.Column("price_per_portion", sa.Float(), nullable=True),
        sa.Column("purchase_price", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_price_logs_product", "product_price_logs", ["product_id", "changed_at"])


def downgrade() -> None:
    op.drop_index("ix_price_logs_product", table_name="product_price_logs")
    op.drop_table("product_price_logs")
