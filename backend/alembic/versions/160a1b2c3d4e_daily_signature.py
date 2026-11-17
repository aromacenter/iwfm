"""Napi aláírás a blokkoláson + céges alapértelmezett munkaidő (160. kör)

Revision ID: 160a1b2c3d4e
Revises: 159a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "160a1b2c3d4e"
down_revision = "159a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("time_entries") as batch:
        batch.add_column(sa.Column("sign_in_data", sa.Text(), nullable=True))
        batch.add_column(sa.Column("sign_out_data", sa.Text(), nullable=True))
    with op.batch_alter_table("work_rules_settings") as batch:
        batch.add_column(sa.Column("default_work_start", sa.Time(), nullable=True))
        batch.add_column(sa.Column("default_work_end", sa.Time(), nullable=True))
        batch.add_column(sa.Column("kiosk_signature", sa.Boolean(), nullable=False, server_default="0"))
    # x-presso (8fba1fe9): a munkaügyi ellenőr napi aláírt jelenlétet kér → ott azonnal be
    op.execute(
        "INSERT INTO work_rules_settings (id, kiosk_signature) SELECT 1, 1 "
        "WHERE NOT EXISTS (SELECT 1 FROM work_rules_settings WHERE id = 1) "
        "AND EXISTS (SELECT 1 FROM worksheet_settings WHERE lower(coalesce(company_name, '')) LIKE '%presso%')"
    )
    op.execute(
        "UPDATE work_rules_settings SET kiosk_signature = 1 WHERE id = 1 "
        "AND EXISTS (SELECT 1 FROM worksheet_settings WHERE lower(coalesce(company_name, '')) LIKE '%presso%')"
    )


def downgrade() -> None:
    with op.batch_alter_table("work_rules_settings") as batch:
        batch.drop_column("kiosk_signature")
        batch.drop_column("default_work_end")
        batch.drop_column("default_work_start")
    with op.batch_alter_table("time_entries") as batch:
        batch.drop_column("sign_out_data")
        batch.drop_column("sign_in_data")
