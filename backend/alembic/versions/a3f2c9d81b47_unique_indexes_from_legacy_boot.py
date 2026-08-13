"""unique indexes from legacy boot block

A régi boot-blokk (main._ensure_employee_code_column) nyers SQL-lel hozta
létre ezeket az egyedi indexeket — az élő adatbázisokban már léteznek, a
migrációkból viszont hiányoztak. IF NOT EXISTS-szel idempotens (SQLite és
PostgreSQL is támogatja), így élesben no-op, friss adatbázison létrehozza.

Revision ID: a3f2c9d81b47
Revises: 8ac1b1644854
Create Date: 2026-08-13
"""
from __future__ import annotations

from alembic import op


revision = 'a3f2c9d81b47'
down_revision = '8ac1b1644854'
branch_labels = None
depends_on = None


INDEXES = (
    ("uq_partners_code", "partners", "partner_code"),
    ("uq_partners_portal_token", "partners", "portal_token"),
    ("uq_assets_qr_token", "assets", "qr_token"),
)


def upgrade() -> None:
    for name, table, column in INDEXES:
        op.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({column})"
        )


def downgrade() -> None:
    for name, _table, _column in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
