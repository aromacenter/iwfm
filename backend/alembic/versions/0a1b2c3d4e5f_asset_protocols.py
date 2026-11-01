"""asset_protocols — kihelyezési / visszavételi / csere jegyzőkönyvek (121. kör)

Revision ID: 0a1b2c3d4e5f
Revises: f2a3b4c5d6e7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0a1b2c3d4e5f"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "asset_protocols",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("serial", sa.String(length=24), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("replacement_asset_id", sa.Uuid(), sa.ForeignKey("assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("partner_id", sa.Uuid(), sa.ForeignKey("partners.id", ondelete="SET NULL"), nullable=True),
        sa.Column("partner_name", sa.String(length=256), nullable=True),
        sa.Column("partner_address", sa.String(length=512), nullable=True),
        sa.Column("partner_tax_number", sa.String(length=32), nullable=True),
        sa.Column("partner_contact", sa.String(length=256), nullable=True),
        sa.Column("location", sa.String(length=512), nullable=True),
        sa.Column("counters", sa.JSON(), nullable=True),
        sa.Column("counter_names", sa.JSON(), nullable=True),
        sa.Column("replacement_counters", sa.JSON(), nullable=True),
        sa.Column("replacement_counter_names", sa.JSON(), nullable=True),
        sa.Column("asset_snapshot", sa.JSON(), nullable=True),
        sa.Column("replacement_snapshot", sa.JSON(), nullable=True),
        sa.Column("on_site", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("signer_name", sa.String(length=128), nullable=True),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("employee_signature", sa.Text(), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_name", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emailed_to", sa.String(length=320), nullable=True),
        sa.UniqueConstraint("serial", name="uq_asset_protocols_serial"),
    )
    op.create_index("ix_asset_protocols_partner", "asset_protocols", ["partner_id", "created_at"])
    op.create_index("ix_asset_protocols_asset", "asset_protocols", ["asset_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_asset_protocols_asset", table_name="asset_protocols")
    op.drop_index("ix_asset_protocols_partner", table_name="asset_protocols")
    op.drop_table("asset_protocols")
