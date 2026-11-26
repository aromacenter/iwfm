"""SLA a szervizjegyeken (185. kör): szerződés/partner/beállítás órák + jegy-határidők

Revision ID: 185a1b2c3d4e
Revises: 184a1b2c3d4e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "185a1b2c3d4e"
down_revision = "184a1b2c3d4e"
branch_labels = None
depends_on = None

COLS = {
    "partner_contracts": [("sla_response_hours", sa.Integer()), ("sla_resolve_hours", sa.Integer())],
    "partners": [("contract_sla_response_hours", sa.Integer()), ("contract_sla_resolve_hours", sa.Integer())],
    "worksheet_settings": [("sla_response_hours", sa.Integer()), ("sla_resolve_hours", sa.Integer())],
    "service_tickets": [
        ("sla_response_due", sa.DateTime(timezone=True)), ("sla_resolve_due", sa.DateTime(timezone=True)),
        ("first_response_at", sa.DateTime(timezone=True)), ("sla_flags", sa.JSON()),
    ],
}


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, cols in COLS.items():
        have = _cols(table)
        with op.batch_alter_table(table) as batch:
            for name, typ in cols:
                if name not in have:
                    batch.add_column(sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    for table, cols in COLS.items():
        have = _cols(table)
        with op.batch_alter_table(table) as batch:
            for name, _ in cols:
                if name in have:
                    batch.drop_column(name)
