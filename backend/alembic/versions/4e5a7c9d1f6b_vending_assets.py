"""Vending: a gépen lévő médiafájlok (gyári reklámképek, termékikonok,
képernyővédő) másolata binárisan — listázás, ZIP-letöltés az adminból.

Revision ID: 4e5a7c9d1f6b
Revises: 3d4f6b8c0e59
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "4e5a7c9d1f6b"
down_revision = "3d4f6b8c0e59"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vending_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "machine_id", sa.Uuid(),
            sa.ForeignKey("vending_machines.id", ondelete="CASCADE", name="fk_vasset_machine"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False, server_default="application/octet-stream"),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("machine_id", "kind", "name", name="uq_vending_asset"),
    )


def downgrade() -> None:
    op.drop_table("vending_assets")
