"""113. kör (hibajegyek 2026-10-28): szerelői átütemezés-kérés a feladaton +
helyszíni átvétel (képernyős aláírások, időbélyeg, e-mail).

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("reschedule_request_note", sa.String(512), nullable=True))
        b.add_column(sa.Column("reschedule_request_date", sa.Date(), nullable=True))
        b.add_column(sa.Column("reschedule_requested_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("machine_intakes") as b:
        b.add_column(sa.Column("on_site", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("client_signature", sa.Text(), nullable=True))
        b.add_column(sa.Column("client_signer_name", sa.String(256), nullable=True))
        b.add_column(sa.Column("employee_signature", sa.Text(), nullable=True))
        b.add_column(sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("machine_intakes") as b:
        for col in ("emailed_at", "signed_at", "employee_signature", "client_signer_name",
                    "client_signature", "on_site"):
            b.drop_column(col)
    with op.batch_alter_table("tasks") as b:
        for col in ("reschedule_requested_at", "reschedule_request_date", "reschedule_request_note"):
            b.drop_column(col)
