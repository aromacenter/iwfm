"""Vending: a gép által feltöltött fájlok (ital-/recept-katalógus = adagok,
rendelés-napló minták) — a Telemetria oldal „Italok és adagok” nézetéhez.

Revision ID: 0a1c3e5b7d33
Revises: f9e1b3c5a031
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0a1c3e5b7d33"
down_revision = "f9e1b3c5a031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vending_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "machine_id", sa.Uuid(),
            sa.ForeignKey("vending_machines.id", ondelete="CASCADE", name="fk_vfile_machine"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vending_files_machine", "vending_files", ["machine_id", "kind", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_vending_files_machine", table_name="vending_files")
    op.drop_table("vending_files")
