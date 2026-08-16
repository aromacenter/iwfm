"""cod payment method

Revision ID: e4b1a7c25f90
Revises: 77a8cc6054b6
Create Date: 2026-08-15

Az utánvét (cod) felvétele a settlements.payment_method check-constraintjébe.
"""

from alembic import op
import sqlalchemy as sa

revision = "e4b1a7c25f90"
down_revision = "77a8cc6054b6"
branch_labels = None
depends_on = None

_OLD = "payment_method IN ('cash','card','transfer')"
_NEW = "payment_method IN ('cash','card','transfer','cod')"


def upgrade() -> None:
    with op.batch_alter_table("settlements") as batch:
        batch.drop_constraint("ck_settlement_payment", type_="check")
        batch.create_check_constraint("ck_settlement_payment", _NEW)


def downgrade() -> None:
    with op.batch_alter_table("settlements") as batch:
        batch.drop_constraint("ck_settlement_payment", type_="check")
        batch.create_check_constraint("ck_settlement_payment", _OLD)
