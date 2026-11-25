"""Számláló-állás átvételkor és átadáskor (173. kör, x-presso 8fb91fca)

Revision ID: 173a1b2c3d4e
Revises: 172a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "173a1b2c3d4e"
down_revision = "172a1b2c3d4e"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _cols("machine_intakes")
    with op.batch_alter_table("machine_intakes") as batch:
        if "counter" not in have:
            batch.add_column(sa.Column("counter", sa.Integer(), nullable=True))
        if "counters" not in have:
            batch.add_column(sa.Column("counters", sa.JSON(), nullable=True))
    have = _cols("worksheets")
    with op.batch_alter_table("worksheets") as batch:
        if "handover_counter" not in have:
            batch.add_column(sa.Column("handover_counter", sa.Integer(), nullable=True))
        if "handover_counters" not in have:
            batch.add_column(sa.Column("handover_counters", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("worksheets") as batch:
        batch.drop_column("handover_counters")
        batch.drop_column("handover_counter")
    with op.batch_alter_table("machine_intakes") as batch:
        batch.drop_column("counters")
        batch.drop_column("counter")
