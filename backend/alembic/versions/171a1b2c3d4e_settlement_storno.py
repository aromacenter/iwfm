"""Számla sztornó mezők az elszámoláson (171. kör)

Revision ID: 171a1b2c3d4e
Revises: 170a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "171a1b2c3d4e"
down_revision = "170a1b2c3d4e"
branch_labels = None
depends_on = None

COLS = [
    ("storno_document_id", sa.String(64)),
    ("storno_document_number", sa.String(64)),
    ("storno_original_number", sa.String(64)),
    ("storno_reason", sa.String(512)),
    ("storno_at", sa.DateTime(timezone=True)),
    ("storno_by_name", sa.String(256)),
]


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("settlements")}
    with op.batch_alter_table("settlements") as batch:
        for name, typ in COLS:
            if name not in have:
                batch.add_column(sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("settlements") as batch:
        for name, _typ in reversed(COLS):
            batch.drop_column(name)
