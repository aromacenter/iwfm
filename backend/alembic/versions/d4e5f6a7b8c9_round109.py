"""109. kör (hibajegyek 2026-10-24): munkalap hibaleírás + megoldás
(tudástár-forrás), nyomtatás-kapcsoló.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("worksheets") as b:
        b.add_column(sa.Column("problem_text", sa.Text(), nullable=True))
        b.add_column(sa.Column("solution_text", sa.Text(), nullable=True))
        b.add_column(sa.Column("print_solution", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("worksheets") as b:
        b.drop_column("print_solution")
        b.drop_column("solution_text")
        b.drop_column("problem_text")
