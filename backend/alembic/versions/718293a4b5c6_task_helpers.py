"""csapatos munkavégzés: task_helpers (136. kör)

Revision ID: 718293a4b5c6
Revises: 6071829304b5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "718293a4b5c6"
down_revision = "6071829304b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_helpers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.Uuid(), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("task_id", "employee_id", name="uq_task_helper"),
    )
    op.create_index("ix_task_helpers_employee", "task_helpers", ["employee_id"])


def downgrade() -> None:
    op.drop_index("ix_task_helpers_employee", table_name="task_helpers")
    op.drop_table("task_helpers")
