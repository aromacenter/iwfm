"""Helyadat a blokkolásnál (191. kör): time_entries.in_geo/out_geo +
work_rules_settings.clock_location/clock_radius_m/phone_clock/clock_sites

Revision ID: 191a1b2c3d4e
Revises: 189a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "191a1b2c3d4e"
down_revision = "189a1b2c3d4e"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _cols("time_entries")
    with op.batch_alter_table("time_entries") as batch:
        if "in_geo" not in have:
            batch.add_column(sa.Column("in_geo", sa.JSON(), nullable=True))
        if "out_geo" not in have:
            batch.add_column(sa.Column("out_geo", sa.JSON(), nullable=True))
    have = _cols("work_rules_settings")
    with op.batch_alter_table("work_rules_settings") as batch:
        if "clock_location" not in have:
            batch.add_column(sa.Column("clock_location", sa.String(16), nullable=False, server_default="off"))
        if "clock_radius_m" not in have:
            batch.add_column(sa.Column("clock_radius_m", sa.Integer(), nullable=False, server_default="300"))
        if "phone_clock" not in have:
            batch.add_column(sa.Column("phone_clock", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "clock_sites" not in have:
            batch.add_column(sa.Column("clock_sites", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("work_rules_settings") as batch:
        batch.drop_column("clock_sites")
        batch.drop_column("phone_clock")
        batch.drop_column("clock_radius_m")
        batch.drop_column("clock_location")
    with op.batch_alter_table("time_entries") as batch:
        batch.drop_column("out_geo")
        batch.drop_column("in_geo")
