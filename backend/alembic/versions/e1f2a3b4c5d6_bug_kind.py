"""bug_reports.kind + priority — fejlesztési ötletek a hibajegy mintájára (118. kör)

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bug_reports") as batch:
        batch.add_column(
            sa.Column("kind", sa.String(length=8), nullable=False, server_default="bug")
        )
        batch.add_column(sa.Column("priority", sa.Integer(), nullable=True))
    op.create_index("ix_bugs_kind", "bug_reports", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_bugs_kind", table_name="bug_reports")
    with op.batch_alter_table("bug_reports") as batch:
        batch.drop_column("priority")
        batch.drop_column("kind")
