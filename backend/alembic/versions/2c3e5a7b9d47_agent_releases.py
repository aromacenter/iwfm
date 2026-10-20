"""Ügynök (X-admin config) APK-kiadások online frissítéshez: az admin tölti fel,
a gép a kulcsával kérdezi/tölti le.

Revision ID: 2c3e5a7b9d47
Revises: 1b2d4f6a8c35
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2c3e5a7b9d47"
down_revision = "1b2d4f6a8c35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_code", sa.Integer(), nullable=False),
        sa.Column("version_name", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(128), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version_code", name="uq_agent_release_code"),
    )


def downgrade() -> None:
    op.drop_table("agent_releases")
