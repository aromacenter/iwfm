"""90. kör mezői:

- partners.contact_email: 320 → 1000 karakter (több cím vesszővel — minden
  számla/értesítés minden címre megy).
- assets.rented: általunk bérelt gép — úgy viselkedik, mint a tárgyi eszköz,
  de a címkékre NEM kerül rá a tulajdonos-felirat.
- assets.swap_pending (JSON): gépcserekor a leszerelt gép ZÁRÓ számlálói +
  a partner — a következő elszámolás ebből számlázza a cseréig lefőzött
  adagokat, utána törlődik.
- employees.is_company: alvállalkozó CÉG (saját dolgozókkal) — a munkalapok
  a cégre szólnak, belül ők osztják be; minden más a külsős szervizessel azonos.
- settlements.late_fee_pct: alkalmazott késedelmi felár (%), None = nincs.

Revision ID: 6a8c0d2e5013
Revises: 5f6b8d0e4011
Create Date: 2026-10-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6a8c0d2e5013"
down_revision = "5f6b8d0e4011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("partners") as b:
        b.alter_column(
            "contact_email",
            existing_type=sa.String(length=320),
            type_=sa.String(length=1000),
            existing_nullable=True,
        )
    op.add_column(
        "assets",
        sa.Column("rented", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("assets", sa.Column("swap_pending", sa.JSON(), nullable=True))
    op.add_column(
        "assets", sa.Column("counters_reset_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "employees",
        sa.Column("is_company", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("settlements", sa.Column("late_fee_pct", sa.Float(), nullable=True))
    op.add_column(
        "machine_intakes", sa.Column("loaner_barcode", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("machine_intakes", "loaner_barcode")
    op.drop_column("settlements", "late_fee_pct")
    op.drop_column("employees", "is_company")
    op.drop_column("assets", "counters_reset_at")
    op.drop_column("assets", "swap_pending")
    op.drop_column("assets", "rented")
    with op.batch_alter_table("partners") as b:
        b.alter_column(
            "contact_email",
            existing_type=sa.String(length=1000),
            type_=sa.String(length=320),
            existing_nullable=True,
        )
