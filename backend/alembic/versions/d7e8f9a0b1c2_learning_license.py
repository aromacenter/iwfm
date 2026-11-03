"""X-Learning csomag a licenc-soron: learning_plan + learning_max_users (142. kör)

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("license_settings") as batch:
        batch.add_column(sa.Column("learning_plan", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("learning_max_users", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("license_settings") as batch:
        batch.drop_column("learning_max_users")
        batch.drop_column("learning_plan")
