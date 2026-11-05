"""Kétlépcsős belépés: login_2fa_mode + login_challenges (147. kör)

Revision ID: 147a2fa0c0de
Revises: f0a1b2c3d4e5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "147a2fa0c0de"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("permission_settings") as batch:
        batch.add_column(sa.Column("login_2fa_mode", sa.String(length=16), nullable=False, server_default="off"))
    op.create_table(
        "login_challenges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sends", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_login_challenges_expires", "login_challenges", ["expires_at"])


def downgrade() -> None:
    op.drop_table("login_challenges")
    with op.batch_alter_table("permission_settings") as batch:
        batch.drop_column("login_2fa_mode")
