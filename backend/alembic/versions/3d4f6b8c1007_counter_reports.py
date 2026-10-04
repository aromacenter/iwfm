"""Partner által bejelentett számláló-állások (counter_reports)

Revision ID: 3d4f6b8c1007
Revises: 2c3e5a7b9005
Create Date: 2026-10-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '3d4f6b8c1007'
down_revision = '2c3e5a7b9005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "counter_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "partner_id", sa.Uuid(),
            sa.ForeignKey("partners.id", ondelete="CASCADE", name="fk_creport_partner"),
            nullable=True,
        ),
        sa.Column(
            "asset_id", sa.Uuid(),
            sa.ForeignKey("assets.id", ondelete="CASCADE", name="fk_creport_asset"),
            nullable=True,
        ),
        sa.Column("barcode", sa.String(64), nullable=True),
        sa.Column("counters", sa.JSON(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("stock_kg", sa.Float(), nullable=True),
        sa.Column("reporter_name", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "settlement_id", sa.Uuid(),
            sa.ForeignKey("settlements.id", ondelete="SET NULL", name="fk_creport_settlement"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_counter_reports_partner", "counter_reports", ["partner_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_counter_reports_partner", table_name="counter_reports")
    op.drop_table("counter_reports")
