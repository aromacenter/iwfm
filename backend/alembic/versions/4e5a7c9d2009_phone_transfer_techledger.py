"""Telefonos elszámolás, pénz-átadás, szerelő-folyószámla

Revision ID: 4e5a7c9d2009
Revises: 3d4f6b8c1007
Create Date: 2026-10-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '4e5a7c9d2009'
down_revision = '3d4f6b8c1007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.add_column(sa.Column(
            "phone_settlement", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))
        batch.add_column(sa.Column("visit_weeks", sa.Integer(), nullable=True))
    with op.batch_alter_table("partners") as batch:
        batch.add_column(sa.Column(
            "contract_phone_settlement", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ))
        batch.add_column(sa.Column("contract_visit_weeks", sa.Integer(), nullable=True))
    op.create_table(
        "cash_transfers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "from_user_id", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_ct_from"),
            nullable=False,
        ),
        sa.Column(
            "to_user_id", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_ct_to"),
            nullable=False,
        ),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_cash_transfers_to", "cash_transfers", ["to_user_id", "status"])
    op.create_table(
        "tech_ledger",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "employee_id", sa.Uuid(),
            sa.ForeignKey("employees.id", ondelete="CASCADE", name="fk_tl_emp"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column("supplier", sa.String(256), nullable=True),
        sa.Column("receipt_no", sa.String(64), nullable=True),
        sa.Column(
            "created_by", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_tl_user"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tech_ledger_emp", "tech_ledger", ["employee_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_tech_ledger_emp", table_name="tech_ledger")
    op.drop_table("tech_ledger")
    op.drop_index("ix_cash_transfers_to", table_name="cash_transfers")
    op.drop_table("cash_transfers")
    with op.batch_alter_table("partners") as batch:
        batch.drop_column("contract_visit_weeks")
        batch.drop_column("contract_phone_settlement")
    with op.batch_alter_table("partner_contracts") as batch:
        batch.drop_column("visit_weeks")
        batch.drop_column("phone_settlement")
