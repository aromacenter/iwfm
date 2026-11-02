"""partner_contracts: billing_cycle / billing_weekday / billing_time (126. kör)

Revision ID: 3d4e5f607182
Revises: 2c3d4e5f6071
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3d4e5f607182"
down_revision = "2c3d4e5f6071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.add_column(
            sa.Column("billing_cycle", sa.String(length=16), nullable=False, server_default="monthly")
        )
        batch.add_column(sa.Column("billing_weekday", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("billing_time", sa.String(length=5), nullable=True))
        batch.add_column(sa.Column("auto_settlement", sa.Boolean(), nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("partner_contracts") as batch:
        batch.drop_column("auto_settlement")
        batch.drop_column("billing_time")
        batch.drop_column("billing_weekday")
        batch.drop_column("billing_cycle")
