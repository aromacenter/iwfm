"""Előfizetési licenc: sáv, limitek, érvényesség

Revision ID: c5d7e9f1a663
Revises: b3e5a7c9d441
Create Date: 2026-08-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c5d7e9f1a663'
down_revision = 'b3e5a7c9d441'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "license_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan", sa.String(length=16), nullable=False, server_default="xl"),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("max_users_override", sa.Integer(), nullable=True),
        sa.Column("max_employees_override", sa.Integer(), nullable=True),
        sa.Column("customer_name", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("license_settings")
