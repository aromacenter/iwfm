"""Alkatrész-raktár B fázis (1d553d2e): beépítés munkalapra (eladási ár
pillanatkép a mozgáson) + jóváhagyásos kiadás-kérések (parts_requests).

Revision ID: 6a7b9c0d1e2f
Revises: 5f6b8d0e2a7c
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "6a7b9c0d1e2f"
down_revision = "5f6b8d0e2a7c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # use-mozgásnál az ügyfél-ár pillanatképe (a cikktörzsből, felülírható)
    with op.batch_alter_table("parts_movements") as b:
        b.add_column(sa.Column("price_net", sa.Float(), nullable=True))

    op.create_table(
        "parts_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("part_id", sa.Uuid(), sa.ForeignKey("parts.id", ondelete="CASCADE", name="fk_parts_req_part"), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_parts_req_user"), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("parts_warehouses.id", ondelete="CASCADE", name="fk_parts_req_wh"), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(512), nullable=True),
        sa.Column("issued_from_warehouse_id", sa.Uuid(), nullable=True),
        sa.Column("issued_from_location", sa.String(16), nullable=True),
        sa.Column("issued_quantity", sa.Float(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','issued','received','declined','cancelled')", name="ck_parts_req_status"
        ),
    )
    op.create_index("ix_parts_requests_status", "parts_requests", ["status", "created_at"])
    op.create_index("ix_parts_requests_requester", "parts_requests", ["requester_user_id"])


def downgrade() -> None:
    op.drop_index("ix_parts_requests_requester", table_name="parts_requests")
    op.drop_index("ix_parts_requests_status", table_name="parts_requests")
    op.drop_table("parts_requests")
    with op.batch_alter_table("parts_movements") as b:
        b.drop_column("price_net")
