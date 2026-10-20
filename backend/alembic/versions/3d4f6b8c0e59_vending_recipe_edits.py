"""Vending: távoli recept-/ármódosítások (vending_recipe_edits) — az eredeti
fájlok mentésével és visszaállíthatóságával.

Revision ID: 3d4f6b8c0e59
Revises: 2c3e5a7b9d47
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3d4f6b8c0e59"
down_revision = "2c3e5a7b9d47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vending_recipe_edits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "machine_id", sa.Uuid(),
            sa.ForeignKey("vending_machines.id", ondelete="CASCADE", name="fk_vredit_machine"),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False, server_default="set"),
        sa.Column("product_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("recipe_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_vending_recipe_edits_machine", "vending_recipe_edits", ["machine_id", "status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_vending_recipe_edits_machine", table_name="vending_recipe_edits")
    op.drop_table("vending_recipe_edits")
