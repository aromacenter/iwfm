"""Tárhely-címke minták (174. kör)

Revision ID: 174a1b2c3d4e
Revises: 173a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "174a1b2c3d4e"
down_revision = "173a1b2c3d4e"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("warehouse_label_templates"):
        op.create_table(
            "warehouse_label_templates",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    if _has_table("warehouse_label_templates"):
        op.drop_table("warehouse_label_templates")
