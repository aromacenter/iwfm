"""kávégép ↔ partner kapcsolat (133. kör, telemetria-alapú útvonal)

- vending_machines: partner_id (FK partners, SET NULL)

Revision ID: 6071829304b5
Revises: 5f60718293a4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6071829304b5"
down_revision = "5f60718293a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("vending_machines") as batch:
        batch.add_column(sa.Column("partner_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_vending_machines_partner", "partners", ["partner_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_vending_machines_partner", ["partner_id"])


def downgrade() -> None:
    with op.batch_alter_table("vending_machines") as batch:
        batch.drop_index("ix_vending_machines_partner")
        batch.drop_constraint("fk_vending_machines_partner", type_="foreignkey")
        batch.drop_column("partner_id")
