"""Fogyás-riasztás, reggeli brief Telegramra, üzemeltetői jelenlét, munkaóra-
önköltség (178–180. kör)

Revision ID: 178a1b2c3d4e
Revises: 177a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "178a1b2c3d4e"
down_revision = "177a1b2c3d4e"
branch_labels = None
depends_on = None

COLS = (
    ("partners", "consumption_alert_at", sa.Column("consumption_alert_at", sa.DateTime(timezone=True), nullable=True)),
    ("notification_settings", "daily_telegram", sa.Column("daily_telegram", sa.Boolean(), nullable=False, server_default="0")),
    ("users", "last_seen_at", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)),
    ("worksheet_settings", "labor_cost_per_hour", sa.Column("labor_cost_per_hour", sa.Float(), nullable=True)),
)


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, name, col in COLS:
        if name not in _cols(table):
            with op.batch_alter_table(table) as batch:
                batch.add_column(col)


def downgrade() -> None:
    for table, name, _col in COLS:
        if name in _cols(table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(name)
