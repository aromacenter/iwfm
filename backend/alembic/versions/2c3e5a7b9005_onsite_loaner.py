"""Helyszíni munkalap + cseregép mezők

Revision ID: 2c3e5a7b9005
Revises: 1b2d4f6a8003
Create Date: 2026-10-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '2c3e5a7b9005'
down_revision = '1b2d4f6a8003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("worksheets") as batch:
        batch.add_column(sa.Column(
            "onsite", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))
        batch.add_column(sa.Column("loaner_asset_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_ws_loaner_asset", "assets", ["loaner_asset_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.add_column(sa.Column("loaner_barcode", sa.String(64), nullable=True))
        batch.add_column(sa.Column("loaner_counters", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("worksheets") as batch:
        batch.drop_constraint("fk_ws_loaner_asset", type_="foreignkey")
        batch.drop_column("loaner_counters")
        batch.drop_column("loaner_barcode")
        batch.drop_column("loaner_asset_id")
        batch.drop_column("onsite")
