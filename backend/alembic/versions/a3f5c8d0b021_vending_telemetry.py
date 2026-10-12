"""Vending telemetria: gép-nyilvántartás (API-kulcsos gép-hitelesítéssel,
utolsó telemetria-pillanatképpel) + telemetria-napló.

Revision ID: a3f5c8d0b021
Revises: 9d1f3b6c8019
Create Date: 2026-10-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3f5c8d0b021"
down_revision = "9d1f3b6c8019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vending_machines",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("machine_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("api_key", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telemetry", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("machine_code", name="uq_vending_machine_code"),
    )
    op.create_table(
        "vending_telemetry",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "machine_id", sa.Uuid(),
            sa.ForeignKey("vending_machines.id", ondelete="CASCADE", name="fk_vtel_machine"),
            nullable=False,
        ),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_vending_telemetry_machine", "vending_telemetry", ["machine_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_vending_telemetry_machine", table_name="vending_telemetry")
    op.drop_table("vending_telemetry")
    op.drop_table("vending_machines")
