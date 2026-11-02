"""fotó-kötelezettség a feladat lezárásához (131. kör)

- worksheet_settings: photo_policy (none|service|all), photo_min

Revision ID: 5f60718293a4
Revises: 4e5f60718293
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5f60718293a4"
down_revision = "4e5f60718293"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("worksheet_settings") as batch:
        batch.add_column(sa.Column("photo_policy", sa.String(length=16), nullable=False, server_default="none"))
        batch.add_column(sa.Column("photo_min", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("worksheet_settings") as batch:
        batch.drop_column("photo_min")
        batch.drop_column("photo_policy")
