"""machine intakes (átvételi elismervény) + szerkeszthető láblécszövegek

Revision ID: b8c4f2a6d915
Revises: 9f3a5c7d1e42
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b8c4f2a6d915'
down_revision = '9f3a5c7d1e42'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "machine_intakes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("serial", sa.String(length=20), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("partner_id", sa.Uuid(), nullable=True),
        sa.Column("client_name", sa.String(length=256), nullable=True),
        sa.Column("client_phone", sa.String(length=64), nullable=True),
        sa.Column("accessories", sa.Text(), nullable=True),
        sa.Column("faults", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("received_by_name", sa.String(length=256), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("serial", name="uq_machine_intakes_serial"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], name="fk_intakes_asset", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["partner_id"], ["partners.id"], name="fk_intakes_partner", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_intakes_user", ondelete="SET NULL"),
    )
    op.add_column("worksheet_settings", sa.Column("customer_footer_text", sa.Text(), nullable=True))
    op.add_column("worksheet_settings", sa.Column("intake_footer_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("worksheet_settings", "intake_footer_text")
    op.drop_column("worksheet_settings", "customer_footer_text")
    op.drop_table("machine_intakes")
