"""Üzemeltetői fiók-kérések a Flotta-pultból + users.is_operator (170. kör)

Revision ID: 170a1b2c3d4e
Revises: 164a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "170a1b2c3d4e"
down_revision = "164a1b2c3d4e"
branch_labels = None
depends_on = None


def _insp():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    have = {c["name"] for c in _insp().get_columns("users")}
    if "is_operator" not in have:
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("is_operator", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "operator_user_requests" not in _insp().get_table_names():
        op.create_table(
            "operator_user_requests",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("kind", sa.String(16), nullable=False, server_default="create"),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("last_name", sa.String(128), nullable=False),
            sa.Column("first_name", sa.String(128), nullable=False),
            sa.Column("email", sa.String(320), nullable=True),
            sa.Column("username", sa.String(64), nullable=True),
            sa.Column("role", sa.String(32), nullable=False, server_default="employee"),
            sa.Column("with_employee", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_operator", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("note", sa.String(512), nullable=True),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
            sa.Column("decline_reason", sa.String(256), nullable=True),
            sa.Column("target_user_id", sa.Uuid(), nullable=True),
            sa.Column("reset_url", sa.Text(), nullable=True),
            sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_opuserreq_status", "operator_user_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_opuserreq_status", table_name="operator_user_requests")
    op.drop_table("operator_user_requests")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_operator")
