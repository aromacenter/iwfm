"""heti vezetői digest + számla-emlékeztető lépcső (128. kör)

- notification_settings: weekly_digest, weekly_day, last_weekly_sent, reminders_enabled, reminder_days, last_reminder_run
- settlements: reminder_stage, reminder_sent_at

Revision ID: 4e5f60718293
Revises: 3d4e5f607182
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "4e5f60718293"
down_revision = "3d4e5f607182"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_settings") as batch:
        batch.add_column(sa.Column("weekly_digest", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("weekly_day", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("last_weekly_sent", sa.String(length=10), nullable=True))
        batch.add_column(sa.Column("reminders_enabled", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("reminder_days", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("last_reminder_run", sa.String(length=10), nullable=True))
    with op.batch_alter_table("settlements") as batch:
        batch.add_column(sa.Column("reminder_stage", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("settlements") as batch:
        batch.drop_column("reminder_sent_at")
        batch.drop_column("reminder_stage")
    with op.batch_alter_table("notification_settings") as batch:
        for col in ("last_reminder_run", "reminder_days", "reminders_enabled", "last_weekly_sent", "weekly_day", "weekly_digest"):
            batch.drop_column(col)
