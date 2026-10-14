"""Belépés felhasználónévvel: users.username (egyedi, opcionális) + az email
opcionálissá válik — minden szerepkörnél elég az egyik azonosító.

Revision ID: d6c8f1a3e027
Revises: c5b7e0f2d025
Create Date: 2026-10-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d6c8f1a3e027"
down_revision = "c5b7e0f2d025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch-mód: SQLite-on tábla-újraépítéssel megy az ALTER COLUMN,
    # Postgresen sima ALTER-ekre fordul.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("username", sa.String(length=64), nullable=True))
        batch.create_unique_constraint("uq_users_username", ["username"])
        batch.alter_column("email", existing_type=sa.String(length=320), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("email", existing_type=sa.String(length=320), nullable=False)
        batch.drop_constraint("uq_users_username", type_="unique")
        batch.drop_column("username")
