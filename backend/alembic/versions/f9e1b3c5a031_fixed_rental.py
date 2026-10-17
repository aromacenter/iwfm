"""Fix bérleti díjas szerződés (1b2a4153/8e843400): havi automata elszámolás
és számla a megadott napon, gépenkénti fix díjjal; azonos adószámú címek
összevonhatók egy számlára.

Revision ID: f9e1b3c5a031
Revises: e8d0a2b4f029
Create Date: 2026-10-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f9e1b3c5a031"
down_revision = "e8d0a2b4f029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("partner_contracts", sa.Column(
        "fixed_rental", sa.Boolean(), nullable=False, server_default=sa.false()
    ))
    op.add_column("partner_contracts", sa.Column("billing_day", sa.Integer(), nullable=True))
    op.add_column("partner_contracts", sa.Column(
        "rental_merge_company", sa.Boolean(), nullable=False, server_default=sa.false()
    ))
    op.add_column("partner_contracts", sa.Column("rental_last_billed", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("partner_contracts", "rental_last_billed")
    op.drop_column("partner_contracts", "rental_merge_company")
    op.drop_column("partner_contracts", "billing_day")
    op.drop_column("partner_contracts", "fixed_rental")
