"""Ügyfél-értékelés a munkalapon (186. kör): worksheets.rating*, rating_token

Revision ID: 186a1b2c3d4e
Revises: 185a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "186a1b2c3d4e"
down_revision = "185a1b2c3d4e"
branch_labels = None
depends_on = None

COLS = [
    ("rating", sa.Integer()), ("rating_comment", sa.Text()), ("rated_at", sa.DateTime(timezone=True)),
    ("rating_source", sa.String(8)), ("rating_token", sa.String(64)),
]


def _cols() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("worksheets")}


def upgrade() -> None:
    have = _cols()
    with op.batch_alter_table("worksheets") as batch:
        for name, typ in COLS:
            if name not in have:
                batch.add_column(sa.Column(name, typ, nullable=True))
    idx = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("worksheets")}
    if "uq_worksheets_rating_token" not in idx:
        op.create_index("uq_worksheets_rating_token", "worksheets", ["rating_token"], unique=True)


def downgrade() -> None:
    idx = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("worksheets")}
    if "uq_worksheets_rating_token" in idx:
        op.drop_index("uq_worksheets_rating_token", table_name="worksheets")
    have = _cols()
    with op.batch_alter_table("worksheets") as batch:
        for name, _ in COLS:
            if name in have:
                batch.drop_column(name)
