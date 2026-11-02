"""work_rules_settings.sunday_bonus_default alapból BE (könyvelői egyeztetés, 125. kör)

Revision ID: 2c3d4e5f6071
Revises: 1b2c3d4e5f60
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2c3d4e5f6071"
down_revision = "1b2c3d4e5f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("work_rules_settings") as batch:
        batch.alter_column("sunday_bonus_default", server_default="1")
    # a néhány napja létrejött sorokon még a régi (kikapcsolt) alap volt
    # Postgres: boolean oszlopot csak boolean literállal (TRUE) lehet beállítani — a `= 1`
    # ott "operator does not exist: boolean = integer" hibát ad; SQLite a TRUE-t is érti.
    op.execute(sa.text("UPDATE work_rules_settings SET sunday_bonus_default = TRUE"))


def downgrade() -> None:
    with op.batch_alter_table("work_rules_settings") as batch:
        batch.alter_column("sunday_bonus_default", server_default="0")
