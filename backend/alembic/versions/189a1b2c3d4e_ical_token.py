"""iCal naptár-előfizetés (189. kör): users.ical_token

Revision ID: 189a1b2c3d4e
Revises: 187a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "189a1b2c3d4e"
down_revision = "187a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "ical_token" not in cols:
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("ical_token", sa.String(64), nullable=True))
    idx = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("users")}
    if "uq_users_ical_token" not in idx:
        op.create_index("uq_users_ical_token", "users", ["ical_token"], unique=True)


def downgrade() -> None:
    idx = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("users")}
    if "uq_users_ical_token" in idx:
        op.drop_index("uq_users_ical_token", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("ical_token")
