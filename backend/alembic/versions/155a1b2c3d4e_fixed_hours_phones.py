"""Rögzített munkaidő a kartonon + értesítő / címke telefonszám (155. kör)

Revision ID: 155a1b2c3d4e
Revises: 154a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "155a1b2c3d4e"
down_revision = "154a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("employees") as batch:
        batch.add_column(sa.Column("fixed_work_start", sa.Time(), nullable=True))
        batch.add_column(sa.Column("fixed_work_end", sa.Time(), nullable=True))
    with op.batch_alter_table("worksheet_settings") as batch:
        batch.add_column(sa.Column("pickup_phone", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("label_phone", sa.String(length=32), nullable=True))
    # c15e4a82: az X-Presso példányon az eddig beégetett szám helyett az új
    # ügyeleti szám — más példányon üres marad (nincs telefonos mondat)
    op.execute(
        "UPDATE worksheet_settings SET pickup_phone = '+36 70 703 40 42', label_phone = '+36 1 700 4042' "
        "WHERE lower(coalesce(company_name, '')) LIKE '%presso%'"
    )


def downgrade() -> None:
    with op.batch_alter_table("worksheet_settings") as batch:
        batch.drop_column("label_phone")
        batch.drop_column("pickup_phone")
    with op.batch_alter_table("employees") as batch:
        batch.drop_column("fixed_work_end")
        batch.drop_column("fixed_work_start")
