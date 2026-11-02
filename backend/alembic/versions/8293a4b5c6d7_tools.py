"""szerszám-nyilvántartás: tools + tool_events (137. kör)

Revision ID: 8293a4b5c6d7
Revises: 718293a4b5c6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8293a4b5c6d7"
down_revision = "718293a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tools",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("serial_no", sa.String(length=128), nullable=True, unique=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("holder_employee_id", sa.Uuid(), sa.ForeignKey("employees.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("next_check", sa.Date(), nullable=True),
        sa.Column("last_check", sa.Date(), nullable=True),
        sa.Column("check_interval_months", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tools_holder", "tools", ["holder_employee_id"])
    op.create_index("ix_tools_next_check", "tools", ["next_check"])
    op.create_table(
        "tool_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tool_id", sa.Uuid(), sa.ForeignKey("tools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("from_employee_id", sa.Uuid(), nullable=True),
        sa.Column("to_employee_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("actor_name", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tool_events_tool", "tool_events", ["tool_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_tool_events_tool", table_name="tool_events")
    op.drop_table("tool_events")
    op.drop_index("ix_tools_next_check", table_name="tools")
    op.drop_index("ix_tools_holder", table_name="tools")
    op.drop_table("tools")
