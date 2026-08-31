"""Gépátvétel-fotók (intake_photos)

Revision ID: d3f5a7b9c119
Revises: c1e3a5b7d997
Create Date: 2026-08-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd3f5a7b9c119'
down_revision = 'c1e3a5b7d997'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intake_photos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "intake_id", sa.Uuid(),
            sa.ForeignKey("machine_intakes.id", ondelete="CASCADE", name="fk_photo_intake"),
            nullable=False,
        ),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column("mime", sa.String(length=32), nullable=False, server_default="image/jpeg"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_intake_photos_intake_id", "intake_photos", ["intake_id"])


def downgrade() -> None:
    op.drop_index("ix_intake_photos_intake_id", table_name="intake_photos")
    op.drop_table("intake_photos")
