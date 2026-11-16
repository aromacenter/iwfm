"""Levélküldés: HTML + csatolmány-nevek (159. kör)

Revision ID: 159a1b2c3d4e
Revises: 158a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "159a1b2c3d4e"
down_revision = "158a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("mail_campaigns") as batch:
        batch.add_column(sa.Column("is_html", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("attachment_names", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mail_campaigns") as batch:
        batch.drop_column("attachment_names")
        batch.drop_column("is_html")
