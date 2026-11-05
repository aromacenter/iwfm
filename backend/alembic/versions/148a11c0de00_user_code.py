"""Fiók-szintű saját szám + Telegram-kapcsolat (148. kör)

Revision ID: 148a11c0de00
Revises: 147a2fa0c0de
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "148a11c0de00"
down_revision = "147a2fa0c0de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("user_code", sa.String(length=6), nullable=True))
        batch.add_column(sa.Column("telegram_chat_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("telegram_chat_id")
        batch.drop_column("user_code")
