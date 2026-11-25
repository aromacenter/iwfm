"""Telegram-összekapcsolás egyszeri kóddal (182. kör): users/employees tg_link_code

Revision ID: 182a1b2c3d4e
Revises: 178a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "182a1b2c3d4e"
down_revision = "178a1b2c3d4e"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table in ("users", "employees"):
        have = _cols(table)
        with op.batch_alter_table(table) as batch:
            if "tg_link_code" not in have:
                batch.add_column(sa.Column("tg_link_code", sa.String(6), nullable=True))
            if "tg_link_code_expires" not in have:
                batch.add_column(sa.Column("tg_link_code_expires", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for table in ("users", "employees"):
        have = _cols(table)
        with op.batch_alter_table(table) as batch:
            if "tg_link_code_expires" in have:
                batch.drop_column("tg_link_code_expires")
            if "tg_link_code" in have:
                batch.drop_column("tg_link_code")
