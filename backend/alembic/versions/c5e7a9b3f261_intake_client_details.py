"""intake client details (cégnév, e-mail, cím)

Revision ID: c5e7a9b3f261
Revises: b8c4f2a6d915
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c5e7a9b3f261'
down_revision = 'b8c4f2a6d915'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("machine_intakes", sa.Column("client_company", sa.String(length=256), nullable=True))
    op.add_column("machine_intakes", sa.Column("client_email", sa.String(length=320), nullable=True))
    op.add_column("machine_intakes", sa.Column("client_address", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("machine_intakes", "client_address")
    op.drop_column("machine_intakes", "client_email")
    op.drop_column("machine_intakes", "client_company")
