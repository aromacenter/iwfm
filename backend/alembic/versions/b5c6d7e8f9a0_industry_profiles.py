"""iparág-profil + példány-szintű modul-rejtés (140. kör)

- license_settings: hidden_modules JSON, industry_profile

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("license_settings") as batch:
        batch.add_column(sa.Column("hidden_modules", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("industry_profile", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("license_settings") as batch:
        batch.drop_column("industry_profile")
        batch.drop_column("hidden_modules")
