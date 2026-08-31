"""CashBook könyvelési feladás (cashbook_settings + settlements oszlopok)

Revision ID: c3f5a7b9d113
Revises: b1d3e5f7a991
Create Date: 2026-08-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c3f5a7b9d113'
down_revision = 'b1d3e5f7a991'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cashbook_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("test_mode", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("supplier_name", sa.String(256), nullable=True),
        sa.Column("supplier_tax_number", sa.String(32), nullable=True),
        sa.Column("supplier_zip", sa.String(16), nullable=True),
        sa.Column("supplier_city", sa.String(128), nullable=True),
        sa.Column("supplier_street", sa.String(256), nullable=True),
        sa.Column("ledger_cash", sa.String(50), nullable=False, server_default="381"),
        sa.Column("ledger_bank", sa.String(50), nullable=False, server_default="384"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    with op.batch_alter_table("settlements") as batch:
        batch.add_column(sa.Column("cashbook_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("cashbook_status", sa.String(16), nullable=True))
        batch.add_column(sa.Column("cashbook_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("settlements") as batch:
        batch.drop_column("cashbook_sent_at")
        batch.drop_column("cashbook_status")
        batch.drop_column("cashbook_hash")
    op.drop_table("cashbook_settings")
