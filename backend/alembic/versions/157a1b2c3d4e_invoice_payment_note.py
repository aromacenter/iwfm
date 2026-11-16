"""Számla fizetési útmutató a beállításokban (157. kör)

Revision ID: 157a1b2c3d4e
Revises: 155a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "157a1b2c3d4e"
down_revision = "155a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("worksheet_settings") as batch:
        batch.add_column(sa.Column("invoice_payment_note", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("worksheet_settings") as batch:
        batch.drop_column("invoice_payment_note")
