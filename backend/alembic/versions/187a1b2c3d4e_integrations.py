"""Integrációk (187. kör): webhook_endpoints + api_keys

Revision ID: 187a1b2c3d4e
Revises: 186a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "187a1b2c3d4e"
down_revision = "186a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "webhook_endpoints" not in tables:
        op.create_table(
            "webhook_endpoints",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("url", sa.String(512), nullable=False),
            sa.Column("secret", sa.String(128), nullable=False),
            sa.Column("events", sa.JSON(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("delivered", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_status", sa.Integer(), nullable=True),
            sa.Column("last_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.String(512), nullable=True),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if "api_keys" not in tables:
        op.create_table(
            "api_keys",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("prefix", sa.String(12), nullable=False),
            sa.Column("key_hash", sa.String(64), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("created_by_name", sa.String(256), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("uq_api_keys_hash", "api_keys", ["key_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_api_keys_hash", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("webhook_endpoints")
