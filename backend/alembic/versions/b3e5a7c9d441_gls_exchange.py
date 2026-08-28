"""GLS csomagcsere (XS) szolgáltatás jelölése a feladásokon

Revision ID: b3e5a7c9d441
Revises: f7c1e3a5b779
Create Date: 2026-08-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b3e5a7c9d441'
down_revision = 'f7c1e3a5b779'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gls_parcels",
        sa.Column(
            "exchange_service", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("gls_parcels", "exchange_service")
