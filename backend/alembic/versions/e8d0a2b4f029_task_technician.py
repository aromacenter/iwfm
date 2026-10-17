"""Alvállalkozó cégen belüli munka-kiadás (87994b7c): a feladathoz
hozzárendelhető a cég szerelője — a munkalap ebből töltődik elő.

Revision ID: e8d0a2b4f029
Revises: d6c8f1a3e027
Create Date: 2026-10-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e8d0a2b4f029"
down_revision = "d6c8f1a3e027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("technician_name", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "technician_name")
