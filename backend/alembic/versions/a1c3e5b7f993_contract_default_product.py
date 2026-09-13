"""Szerződéses alapértelmezett kávé (partner_contracts.default_product_id)

Revision ID: a1c3e5b7f993
Revises: f9b1d3e5c779
Create Date: 2026-09-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1c3e5b7f993'
down_revision = 'f9b1d3e5c779'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.add_column(sa.Column("default_product_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_partner_contracts_product", "products", ["default_product_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.drop_constraint("fk_partner_contracts_product", type_="foreignkey")
        batch.drop_column("default_product_id")
