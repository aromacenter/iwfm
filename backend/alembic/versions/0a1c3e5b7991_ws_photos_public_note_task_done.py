"""Munkalap-fotók + szervizes ügyfél-megjegyzés + feladat-elvégzés bélyegzők

Revision ID: 0a1c3e5b7991
Revises: f0b2d4e6a551
Create Date: 2026-10-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0a1c3e5b7991'
down_revision = 'f0b2d4e6a551'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("worksheets") as batch:
        batch.add_column(sa.Column("public_note", sa.Text(), nullable=True))
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("completed_by_name", sa.String(256), nullable=True))
    with op.batch_alter_table("partner_contracts") as batch:
        batch.add_column(sa.Column("invoice_norma", sa.Integer(), nullable=True))
    op.create_table(
        "worksheet_photos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "worksheet_id", sa.Uuid(),
            sa.ForeignKey("worksheets.id", ondelete="CASCADE", name="fk_photo_worksheet"),
            nullable=False, index=True,
        ),
        sa.Column("image", sa.LargeBinary(), nullable=False),
        sa.Column("mime", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worksheet_photos")
    with op.batch_alter_table("partner_contracts") as batch:
        batch.drop_column("invoice_norma")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("completed_by_name")
        batch.drop_column("completed_at")
    with op.batch_alter_table("worksheets") as batch:
        batch.drop_column("public_note")
