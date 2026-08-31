"""Szervizjegy→feladat kapcsolat (tasks.service_ticket_id)

Revision ID: e5b7c9d1f331
Revises: d3f5a7b9c119
Create Date: 2026-08-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e5b7c9d1f331'
down_revision = 'd3f5a7b9c119'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("service_ticket_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_task_ticket", "service_tickets", ["service_ticket_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_task_ticket", type_="foreignkey")
        batch.drop_column("service_ticket_id")
