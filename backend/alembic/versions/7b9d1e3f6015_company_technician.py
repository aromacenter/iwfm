"""Alvállalkozó cég v2 (c7ef9ce9 reopened):

- employees.company_name: a cég neve (a vezeték-/keresztnév a kapcsolattartó).
- worksheets.technician_name: a cégen BELÜLI szerelő neve, aki a munkát
  ténylegesen végezte — a díjak a cég közös folyószámláján maradnak, de a
  bontás szerelőnként látszik.

Revision ID: 7b9d1e3f6015
Revises: 6a8c0d2e5013
Create Date: 2026-10-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7b9d1e3f6015"
down_revision = "6a8c0d2e5013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("company_name", sa.String(length=256), nullable=True))
    op.add_column("worksheets", sa.Column("technician_name", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("worksheets", "technician_name")
    op.drop_column("employees", "company_name")
