"""Áremelés / indexálás (184. kör): price_indexations

Revision ID: 184a1b2c3d4e
Revises: 182a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "184a1b2c3d4e"
down_revision = "182a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "price_indexations" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "price_indexations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("percent", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("round_to", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("targets", sa.JSON(), nullable=True),
        sa.Column("partner_ids", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="scheduled"),
        sa.Column("rows", sa.JSON(), nullable=True),
        sa.Column("notice_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notice_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_by_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_price_indexations_status", "price_indexations", ["status", "effective_from"])


def downgrade() -> None:
    op.drop_index("ix_price_indexations_status", table_name="price_indexations")
    op.drop_table("price_indexations")
