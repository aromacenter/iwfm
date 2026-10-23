"""RBAC v2: saját szerepkörök (roles tábla), művelet-szintű jogok
(permission_settings.actions), a users.role check-constraint megszűnik.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-23
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

SYSTEM_ROLES = (
    ("admin", "Admin", "Mindent elér, minden beállítást kezel. Nem szűkíthető.", 2),
    ("manager", "Manager", "Vezetői szint: feladatok, beosztás, elszámolás, raktár, kassza-áttekintés.", 1),
    ("uzletkoto", "Üzletkötő", "Partnerek, gépek, elszámolás, saját kassza — terepi értékesítő.", 0),
    ("szervizes", "Szervizes", "Külsős szerviz: átvétel, tudásbázis, saját KSZ-munkalapok.", 0),
    ("employee", "Dolgozó", "Önkiszolgáló: saját beosztás, saját feladatok.", 0),
)


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("key", name="uq_roles_key"),
    )
    roles = sa.table(
        "roles",
        sa.column("id", sa.Uuid()), sa.column("key", sa.String()), sa.column("name", sa.String()),
        sa.column("description", sa.Text()), sa.column("level", sa.Integer()), sa.column("is_system", sa.Boolean()),
    )
    op.bulk_insert(roles, [
        {"id": uuid.uuid4(), "key": k, "name": n, "description": d, "level": lv, "is_system": True}
        for k, n, d, lv in SYSTEM_ROLES
    ])
    with op.batch_alter_table("permission_settings") as b:
        b.add_column(sa.Column("actions", sa.JSON(), nullable=True))
    # users.role: bármely roles.key lehet → a régi check megszűnik
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("users", recreate="always") as b:
            b.drop_constraint("ck_users_role", type_="check")
    else:
        op.drop_constraint("ck_users_role", "users", type_="check")


def downgrade() -> None:
    check = "role IN ('admin','manager','uzletkoto','szervizes','employee')"
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("users", recreate="always") as b:
            b.create_check_constraint("ck_users_role", check)
    else:
        op.create_check_constraint("ck_users_role", "users", check)
    with op.batch_alter_table("permission_settings") as b:
        b.drop_column("actions")
    op.drop_table("roles")
