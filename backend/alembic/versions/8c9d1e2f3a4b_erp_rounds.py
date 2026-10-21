"""ERP-kiegészítő körök (1d553d2e folytatás, 2026-10-21): sorozatszám +
garancia + gép-kapcsolat a beépítéseken, autónkénti min/max, beszállítók +
beszerzési rendelések, mozgó átlagár, karantén + visszáru, kit + helyettesítő
+ kompatibilitás, leltár-munkamenetek (vak leltár, okkódok), raktáronkénti
jog, timer időtípusok + korrekció.

Revision ID: 8c9d1e2f3a4b
Revises: 7b8c0d1e2f3a
Create Date: 2026-10-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8c9d1e2f3a4b"
down_revision = "7b8c0d1e2f3a"
branch_labels = None
depends_on = None

MV_KINDS = "kind IN ('receipt','issue','use','transfer','adjust','return','scrap')"
WH_KINDS = "kind IN ('central','vehicle','quarantine')"


def _replace_check(table: str, name: str, new_sql: str) -> None:
    """Check-constraint csere: Postgresen közvetlenül, SQLite-on batch
    (tábla-újraépítés) módban."""
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as b:
            b.drop_constraint(name, type_="check")
            b.create_check_constraint(name, new_sql)
    else:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, new_sql)


def upgrade() -> None:
    # ── beszállítók (a parts.supplier_id FK miatt elsőként) ──
    op.create_table(
        "parts_suppliers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("contact", sa.String(256), nullable=True),
        sa.Column("lead_days", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "parts_supplier_prices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("supplier_id", sa.Uuid(), sa.ForeignKey("parts_suppliers.id", ondelete="CASCADE", name="fk_psp_supplier"), nullable=False),
        sa.Column("part_id", sa.Uuid(), sa.ForeignKey("parts.id", ondelete="CASCADE", name="fk_psp_part"), nullable=False),
        sa.Column("price_net", sa.Float(), nullable=False),
        sa.Column("supplier_sku", sa.String(64), nullable=True),
        sa.Column("moq", sa.Float(), nullable=False, server_default="1"),
        sa.Column("lead_days", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("supplier_id", "part_id", name="uq_psp_supplier_part"),
    )

    # ── cikktörzs bővítés ──
    with op.batch_alter_table("parts") as b:
        b.add_column(sa.Column("warranty_months", sa.Integer(), nullable=True))
        b.add_column(sa.Column("supplier_id", sa.Uuid(), nullable=True))
        b.add_column(sa.Column("reorder_qty", sa.Float(), nullable=True))
        b.add_column(sa.Column("avg_cost", sa.Float(), nullable=True))
        b.add_column(sa.Column("pack_unit", sa.String(16), nullable=True))
        b.add_column(sa.Column("pack_qty", sa.Float(), nullable=True))
        b.add_column(sa.Column("compatible_models", sa.JSON(), nullable=True))
        b.add_column(sa.Column("substitutes", sa.JSON(), nullable=True))
        b.add_column(sa.Column("abc_class", sa.String(1), nullable=True))

    # ── mozgások: sorozatszám, garancia, gép, okkód, új fajták ──
    with op.batch_alter_table("parts_movements") as b:
        b.add_column(sa.Column("serial_no", sa.String(64), nullable=True))
        b.add_column(sa.Column("warranty_until", sa.Date(), nullable=True))
        b.add_column(sa.Column("asset_id", sa.Uuid(), nullable=True))
        b.add_column(sa.Column("reason", sa.String(24), nullable=True))
    _replace_check("parts_movements", "ck_parts_mv_kind", MV_KINDS)
    op.create_index("ix_parts_movements_asset", "parts_movements", ["asset_id"])

    # ── raktárak: karantén fajta + raktáronkénti kezelők ──
    with op.batch_alter_table("parts_warehouses") as b:
        b.add_column(sa.Column("managers", sa.JSON(), nullable=True))
    _replace_check("parts_warehouses", "ck_parts_wh_kind", WH_KINDS)

    # ── autónkénti min/max ──
    op.create_table(
        "parts_vehicle_levels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("parts_warehouses.id", ondelete="CASCADE", name="fk_pvl_wh"), nullable=False),
        sa.Column("part_id", sa.Uuid(), sa.ForeignKey("parts.id", ondelete="CASCADE", name="fk_pvl_part"), nullable=False),
        sa.Column("min_qty", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_qty", sa.Float(), nullable=False, server_default="0"),
        sa.UniqueConstraint("warehouse_id", "part_id", name="uq_pvl_wh_part"),
    )

    # ── beszerzési rendelések ──
    op.create_table(
        "parts_purchase_orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), sa.ForeignKey("parts_suppliers.id", ondelete="SET NULL", name="fk_ppo_supplier"), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("expected_date", sa.Date(), nullable=True),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("number", name="uq_ppo_number"),
        sa.CheckConstraint("status IN ('draft','ordered','partial','received','cancelled')", name="ck_ppo_status"),
    )
    op.create_table(
        "parts_purchase_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("po_id", sa.Uuid(), sa.ForeignKey("parts_purchase_orders.id", ondelete="CASCADE", name="fk_ppi_po"), nullable=False),
        sa.Column("part_id", sa.Uuid(), sa.ForeignKey("parts.id", ondelete="CASCADE", name="fk_ppi_part"), nullable=False),
        sa.Column("qty_ordered", sa.Float(), nullable=False),
        sa.Column("qty_received", sa.Float(), nullable=False, server_default="0"),
        sa.Column("unit_cost", sa.Float(), nullable=True),
    )
    op.create_index("ix_ppi_po", "parts_purchase_items", ["po_id"])

    # ── visszáru / garanciaigény ──
    op.create_table(
        "parts_returns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("part_id", sa.Uuid(), sa.ForeignKey("parts.id", ondelete="CASCADE", name="fk_pret_part"), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), sa.ForeignKey("parts_suppliers.id", ondelete="SET NULL", name="fk_pret_supplier"), nullable=True),
        sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("parts_warehouses.id", ondelete="SET NULL", name="fk_pret_wh"), nullable=True),
        sa.Column("location", sa.String(16), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("serial_no", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(512), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("outcome_note", sa.String(512), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open','sent','credited','replaced','rejected','scrapped')", name="ck_pret_status"),
    )

    # ── kitek ──
    op.create_table(
        "parts_kits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("sku", name="uq_parts_kits_sku"),
    )
    op.create_table(
        "parts_kit_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kit_id", sa.Uuid(), sa.ForeignKey("parts_kits.id", ondelete="CASCADE", name="fk_pki_kit"), nullable=False),
        sa.Column("part_id", sa.Uuid(), sa.ForeignKey("parts.id", ondelete="CASCADE", name="fk_pki_part"), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False, server_default="1"),
        sa.UniqueConstraint("kit_id", "part_id", name="uq_pki_kit_part"),
    )

    # ── leltár-munkamenetek ──
    op.create_table(
        "parts_stocktakes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("parts_warehouses.id", ondelete="CASCADE", name="fk_pst_wh"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("blind", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("scope", sa.String(16), nullable=False, server_default="all"),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("threshold_pct", sa.Float(), nullable=False, server_default="10"),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open','submitted','approved','cancelled')", name="ck_pst_status"),
    )

    # ── timer: korrekció ──
    with op.batch_alter_table("worksheet_timers") as b:
        b.add_column(sa.Column("correction_seconds", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("correction_note", sa.String(512), nullable=True))
        b.add_column(sa.Column("correction_status", sa.String(12), nullable=False, server_default="none"))
        b.add_column(sa.Column("correction_by", sa.Uuid(), nullable=True))
        b.add_column(sa.Column("correction_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("worksheet_timers") as b:
        for col in ("correction_at", "correction_by", "correction_status", "correction_note", "correction_seconds"):
            b.drop_column(col)
    for tbl in ("parts_stocktakes", "parts_kit_items", "parts_kits", "parts_returns", "parts_purchase_items",
                "parts_purchase_orders", "parts_vehicle_levels"):
        op.drop_table(tbl)
    _replace_check("parts_warehouses", "ck_parts_wh_kind", "kind IN ('central','vehicle')")
    with op.batch_alter_table("parts_warehouses") as b:
        b.drop_column("managers")
    op.drop_index("ix_parts_movements_asset", table_name="parts_movements")
    _replace_check("parts_movements", "ck_parts_mv_kind", "kind IN ('receipt','issue','use','transfer','adjust')")
    with op.batch_alter_table("parts_movements") as b:
        for col in ("reason", "asset_id", "warranty_until", "serial_no"):
            b.drop_column(col)
    with op.batch_alter_table("parts") as b:
        for col in ("abc_class", "substitutes", "compatible_models", "pack_qty", "pack_unit", "avg_cost",
                    "reorder_qty", "supplier_id", "warranty_months"):
            b.drop_column(col)
    op.drop_table("parts_supplier_prices")
    op.drop_table("parts_suppliers")
