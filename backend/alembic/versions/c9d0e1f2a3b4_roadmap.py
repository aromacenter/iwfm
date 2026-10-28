"""115. kör: termék-roadmap a licenc-soron (a Flotta-pult nyomja le).

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("license_settings") as b:
        b.add_column(sa.Column("roadmap", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("license_settings") as b:
        b.drop_column("roadmap")
