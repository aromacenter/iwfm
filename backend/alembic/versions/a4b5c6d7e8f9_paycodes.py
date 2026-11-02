"""bérprogram jogcím-kódok a munkaidő-szabályokban (139. kör)

- work_rules_settings: paycodes JSON

Revision ID: a4b5c6d7e8f9
Revises: 93a4b5c6d7e8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a4b5c6d7e8f9"
down_revision = "93a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("work_rules_settings") as batch:
        batch.add_column(sa.Column("paycodes", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("work_rules_settings") as batch:
        batch.drop_column("paycodes")
