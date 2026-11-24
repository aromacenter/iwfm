"""Szerződés létrehozás-ideje az automata számlázás első futásához (172. kör)

Revision ID: 172a1b2c3d4e
Revises: 171a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "172a1b2c3d4e"
down_revision = "171a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("partner_contracts")}
    if "created_at" not in have:
        with op.batch_alter_table("partner_contracts") as batch:
            batch.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.drop_column("created_at")
