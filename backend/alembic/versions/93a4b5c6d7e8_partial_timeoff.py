"""korábban megyek / később jövök kérelem (138. kör)

- time_off_requests: at_time, minutes; a type-ellenőrzés bővül
  (late_arrival, early_leave)

Revision ID: 93a4b5c6d7e8
Revises: 8293a4b5c6d7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "93a4b5c6d7e8"
down_revision = "8293a4b5c6d7"
branch_labels = None
depends_on = None

OLD = "type IN ('annual','sick','unpaid','other')"
NEW = "type IN ('annual','sick','unpaid','other','late_arrival','early_leave')"


def upgrade() -> None:
    with op.batch_alter_table("time_off_requests", recreate="auto") as batch:
        batch.add_column(sa.Column("at_time", sa.String(length=5), nullable=True))
        batch.add_column(sa.Column("minutes", sa.Integer(), nullable=True))
        batch.drop_constraint("ck_timeoff_type", type_="check")
        batch.create_check_constraint("ck_timeoff_type", NEW)


def downgrade() -> None:
    op.execute("DELETE FROM time_off_requests WHERE type IN ('late_arrival','early_leave')")
    with op.batch_alter_table("time_off_requests", recreate="auto") as batch:
        batch.drop_constraint("ck_timeoff_type", type_="check")
        batch.create_check_constraint("ck_timeoff_type", OLD)
        batch.drop_column("minutes")
        batch.drop_column("at_time")
