"""Átvétel→feladat kapcsolat (tasks.intake_id)

Revision ID: d5f7b9c1e335
Revises: c3f5a7b9d113
Create Date: 2026-08-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd5f7b9c1e335'
down_revision = 'c3f5a7b9d113'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("intake_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_task_intake", "machine_intakes", ["intake_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_task_intake", type_="foreignkey")
        batch.drop_column("intake_id")
