"""Alvállalkozó CÉG szerelő-névsora (d8bde9e6): a cég a munkalapon ebből
választja ki, melyik munkatársa végzi a munkát; az új név automatikusan
felkerül a listára.

Revision ID: b4a6d9e1c023
Revises: a3f5c8d0b021
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4a6d9e1c023"
down_revision = "a3f5c8d0b021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("company_technicians", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("employees", "company_technicians")
