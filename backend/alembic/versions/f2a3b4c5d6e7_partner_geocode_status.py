"""partners.geocode_attempted_at + geocode_error (120. kör, térkép-lista)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("partners") as batch:
        batch.add_column(sa.Column("geocode_attempted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("geocode_error", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("partners") as batch:
        batch.drop_column("geocode_error")
        batch.drop_column("geocode_attempted_at")
