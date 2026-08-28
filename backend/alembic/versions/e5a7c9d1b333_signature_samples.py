"""aláírás-minták + kötelező aláíró-név (munkalap/elszámolás)

Revision ID: e5a7c9d1b333
Revises: c8e3a7f5d219
Create Date: 2026-08-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e5a7c9d1b333'
down_revision = 'c8e3a7f5d219'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("signature_sample", sa.Text(), nullable=True))
    op.add_column(
        "worksheets", sa.Column("client_signer_name", sa.String(length=256), nullable=True)
    )
    op.add_column(
        "settlements", sa.Column("signer_name", sa.String(length=256), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("settlements", "signer_name")
    op.drop_column("worksheets", "client_signer_name")
    op.drop_column("users", "signature_sample")
