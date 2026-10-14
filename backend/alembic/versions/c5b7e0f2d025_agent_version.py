"""Nyomtató-ügynök verzió-jelentése (X-Agent-Version): ebből látszik, hogy
az önfrissítés (v3 pehelysúlyú mód) megtörtént-e az üzleti PC-n.

Revision ID: c5b7e0f2d025
Revises: b4a6d9e1c023
Create Date: 2026-10-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5b7e0f2d025"
down_revision = "b4a6d9e1c023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("print_settings", sa.Column("agent_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("print_settings", "agent_version")
