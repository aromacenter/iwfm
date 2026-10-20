"""Vending promóció: géphez feltöltött reklámképek (MakingImage / ScreenProtect),
amelyeket az ügynök a gépre másol és visszajelez; gépenként kizárólagos-kapcsoló.
Vending hibajelentés: a gép által jelentett, összevont hibák (vending_errors).

Revision ID: 1b2d4f6a8c35
Revises: 0a1c3e5b7d33
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "1b2d4f6a8c35"
down_revision = "0a1c3e5b7d33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vending_promos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "machine_id", sa.Uuid(),
            sa.ForeignKey("vending_machines.id", ondelete="CASCADE", name="fk_vpromo_machine"),
            nullable=False,
        ),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("filename", sa.String(128), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False, server_default="image/jpeg"),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("width", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("thumb", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_vending_promos_machine", "vending_promos", ["machine_id", "slot", "sort_order"])
    op.create_table(
        "vending_errors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "machine_id", sa.Uuid(),
            sa.ForeignKey("vending_machines.id", ondelete="CASCADE", name="fk_verr_machine"),
            nullable=False,
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("message", sa.String(512), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("machine_id", "fingerprint", name="uq_vending_error_fp"),
    )
    op.create_index("ix_vending_errors_machine", "vending_errors", ["machine_id", "resolved_at", "last_seen"])
    with op.batch_alter_table("vending_machines") as batch:
        batch.add_column(sa.Column(
            "promo_exclusive_making", sa.Boolean(), nullable=False, server_default=sa.false()
        ))
        batch.add_column(sa.Column(
            "promo_exclusive_screensaver", sa.Boolean(), nullable=False, server_default=sa.false()
        ))


def downgrade() -> None:
    with op.batch_alter_table("vending_machines") as batch:
        batch.drop_column("promo_exclusive_screensaver")
        batch.drop_column("promo_exclusive_making")
    op.drop_index("ix_vending_errors_machine", table_name="vending_errors")
    op.drop_table("vending_errors")
    op.drop_index("ix_vending_promos_machine", table_name="vending_promos")
    op.drop_table("vending_promos")
