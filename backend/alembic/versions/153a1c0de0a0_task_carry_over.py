"""Feladat-átcsúsztatás (153. kör)

Revision ID: 153a1c0de0a0
Revises: 152a51c0de00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "153a1c0de0a0"
down_revision = "152a51c0de00"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("carry_over_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("original_due_date", sa.Date(), nullable=True))
    with op.batch_alter_table("notification_settings") as batch:
        batch.add_column(sa.Column("task_carry_over", sa.Boolean(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("last_carry_over", sa.String(length=10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("notification_settings") as batch:
        batch.drop_column("last_carry_over")
        batch.drop_column("task_carry_over")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("original_due_date")
        batch.drop_column("carry_over_count")
