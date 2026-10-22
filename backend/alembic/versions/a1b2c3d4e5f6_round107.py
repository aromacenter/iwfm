"""107. kör (hibajegyek 2026-10-22): átadás átutalással (határidő), partner
időkapu + feladat időkapu/átütemezés-indok, elszámolás számla-e-mailek.

Revision ID: a1b2c3d4e5f6
Revises: 9d0e1f2a3b4c
Create Date: 2026-10-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "9d0e1f2a3b4c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("partners") as b:
        b.add_column(sa.Column("visit_window", sa.String(64), nullable=True))
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("visit_window", sa.String(64), nullable=True))
        b.add_column(sa.Column("reschedule_note", sa.String(512), nullable=True))
    with op.batch_alter_table("settlements") as b:
        b.add_column(sa.Column("invoice_emails", sa.String(512), nullable=True))
    with op.batch_alter_table("worksheets") as b:
        b.add_column(sa.Column("handover_due_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("worksheets") as b:
        b.drop_column("handover_due_days")
    with op.batch_alter_table("settlements") as b:
        b.drop_column("invoice_emails")
    with op.batch_alter_table("tasks") as b:
        b.drop_column("reschedule_note")
        b.drop_column("visit_window")
    with op.batch_alter_table("partners") as b:
        b.drop_column("visit_window")
