"""partner_contracts.rent_fee — a fix bérleti díj a szerződésen (124. kör)

Revision ID: 1b2c3d4e5f60
Revises: 0a1b2c3d4e5f
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "1b2c3d4e5f60"
down_revision = "0a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.add_column(sa.Column("rent_fee", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.drop_column("rent_fee")
