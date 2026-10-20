"""Munkaidő-mérés munkalaponként (1d553d2e, C fázis): worksheet_timers —
automatikus indulás a munkalap megnyitásakor, szünet, zárás; a szerelő
csak azt látja, hogy fut, a számok a vezetői statisztikában.

Revision ID: 7b8c0d1e2f3a
Revises: 6a7b9c0d1e2f
Create Date: 2026-10-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7b8c0d1e2f3a"
down_revision = "6a7b9c0d1e2f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worksheet_timers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("worksheet_id", sa.Uuid(), sa.ForeignKey("worksheets.id", ondelete="CASCADE", name="fk_ws_timer_ws"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_ws_timer_user"), nullable=False),
        sa.Column("sessions", sa.JSON(), nullable=False),
        sa.Column("active_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("pause_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("first_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("worksheet_id", "user_id", name="uq_ws_timer_ws_user"),
    )
    op.create_index("ix_ws_timers_started", "worksheet_timers", ["first_started_at"])


def downgrade() -> None:
    op.drop_index("ix_ws_timers_started", table_name="worksheet_timers")
    op.drop_table("worksheet_timers")
