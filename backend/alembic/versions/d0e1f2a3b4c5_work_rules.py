"""work rules: munkaidő-nyilvántartás (117. kör)

- employees: site, sunday_bonus, shift_bonus, bonus_in_base_salary, break_rule
- license_settings: labor_rules_override
- work_rules_settings: új tábla (céges szünet-/pótlék-alapértékek)

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("employees") as batch:
        batch.add_column(sa.Column("site", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("sunday_bonus", sa.Boolean(), nullable=True))
        batch.add_column(
            sa.Column("shift_bonus", sa.Boolean(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "bonus_in_base_salary", sa.Boolean(), nullable=False, server_default="0"
            )
        )
        batch.add_column(sa.Column("break_rule", sa.JSON(), nullable=True))
    with op.batch_alter_table("license_settings") as batch:
        batch.add_column(sa.Column("labor_rules_override", sa.JSON(), nullable=True))
    op.create_table(
        "work_rules_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("break_minutes", sa.Integer(), nullable=True),
        sa.Column("break_per_hours", sa.Integer(), nullable=True),
        sa.Column("break_paid", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("sunday_bonus_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("group_by_site", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("absence_codes", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("work_rules_settings")
    with op.batch_alter_table("license_settings") as batch:
        batch.drop_column("labor_rules_override")
    with op.batch_alter_table("employees") as batch:
        for col in ("break_rule", "bonus_in_base_salary", "shift_bonus", "sunday_bonus", "site"):
            batch.drop_column(col)
