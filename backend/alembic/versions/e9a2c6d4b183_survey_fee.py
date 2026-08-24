"""survey fee (felmérési díj a "nem kérem a javítást" opcióhoz)

Revision ID: e9a2c6d4b183
Revises: d7f1b4c8a352
Create Date: 2026-08-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e9a2c6d4b183'
down_revision = 'd7f1b4c8a352'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worksheet_settings", sa.Column("survey_fee", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("worksheet_settings", "survey_fee")
