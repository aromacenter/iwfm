"""Számlázz.hu szolgáltató a számlázó-beállításokban

Revision ID: b1d3e5f7a991
Revises: a9b1c3d5e779
Create Date: 2026-08-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b1d3e5f7a991'
down_revision = 'a9b1c3d5e779'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("billingo_settings") as batch:
        batch.add_column(sa.Column(
            "provider", sa.String(16), nullable=False, server_default="billingo"
        ))
        batch.add_column(sa.Column("szamlazz_agent_key_encrypted", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("szamlazz_prefix", sa.String(32), nullable=True))
        batch.add_column(sa.Column("pc_szamlazz_agent_key_encrypted", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("pc_szamlazz_prefix", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("billingo_settings") as batch:
        batch.drop_column("pc_szamlazz_prefix")
        batch.drop_column("pc_szamlazz_agent_key_encrypted")
        batch.drop_column("szamlazz_prefix")
        batch.drop_column("szamlazz_agent_key_encrypted")
        batch.drop_column("provider")
