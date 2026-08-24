"""worksheet handover (átadás az ügyfélnek)

Revision ID: a3c9e1f5b627
Revises: f2b8d5a7c419
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a3c9e1f5b627'
down_revision = 'f2b8d5a7c419'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # az assets.status CHECK bővítése a 'handed_over' (átadott) értékkel
    with op.batch_alter_table("assets") as batch:
        batch.drop_constraint("ck_assets_status", type_="check")
        batch.create_check_constraint(
            "ck_assets_status",
            "status IN ('in_stock','deployed','maintenance','retired','handed_over')",
        )
    op.add_column("worksheets", sa.Column("handed_over_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("worksheets", sa.Column("handover_payment_method", sa.String(length=16), nullable=True))
    op.add_column(
        "worksheets",
        sa.Column("handover_discount", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("worksheets", sa.Column("handover_total_net", sa.Float(), nullable=True))
    op.add_column("worksheets", sa.Column("handover_document_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    for col in (
        "handover_document_id", "handover_total_net", "handover_discount",
        "handover_payment_method", "handed_over_at",
    ):
        op.drop_column("worksheets", col)
