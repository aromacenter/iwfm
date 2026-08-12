"""Iwfm backend — FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    admin_tools,
    assistant as assistant_api,
    audit,
    auth,
    automation as automation_api,
    consignment,
    dashboard,
    employees,
    geo,
    import_export,
    inventory,
    kiosk,
    knowledge,
    me,
    orders,
    payroll,
    portal,
    service,
    support,
    settings as settings_api,
    shifts,
    tasks,
    timeclock,
    timeoff,
)
from app.core.config import get_settings
from app.db import get_engine
from app.models import Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


BASELINE_REVISION = "1761410c3a1e"  # a v0.19-es Alembic-átálláskori teljes séma


def _run_alembic(sync_conn) -> None:
    """Sémaverziózás Alembickel (v0.19-től).

    - Már élő, Alembic előtti adatbázis (van users tábla, nincs alembic_version):
      bélyegzés (stamp) DDL nélkül, majd upgrade. ÁTMENETI ág — a bélyegzés
      célpontját a séma állapotából ismerjük fel (marker: az első baseline
      utáni migráció egy oszlopa), hogy a stamp ne ugorjon át valós migrációt.
    - Friss vagy már verziózott adatbázis: upgrade head.
    A régi ensure_column-blokk átmenetileg védőhálóként utána is lefut.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    root = Path(__file__).resolve().parent.parent  # backend/
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.attributes["connection"] = sync_conn

    inspector = inspect(sync_conn)
    tables = inspector.get_table_names()
    if "alembic_version" not in tables and "users" in tables:
        product_cols = (
            {c["name"] for c in inspector.get_columns("products")}
            if "products" in tables
            else set()
        )
        # Ha a 0002-es migráció oszlopa már megvan (create_all hozta létre),
        # a séma aktuális → head; különben baseline, és az upgrade pótolja.
        target = "head" if "purchase_price" in product_cols else BASELINE_REVISION
        command.stamp(cfg, target)
        logger.info("Alembic: existing database stamped to %s (no DDL executed)", target)
    command.upgrade(cfg, "head")


def _ensure_employee_code_column(sync_conn) -> None:
    """Mini-migrációk: a create_all meglévő táblát nem módosít, ezért a már
    futó adatbázisokon az új oszlopokat kézzel adjuk hozzá (SQLite + PG)."""
    from sqlalchemy import inspect, text as sql_text

    inspector = inspect(sync_conn)
    tables = inspector.get_table_names()

    def ensure_column(table: str, column: str, ddl_type: str) -> None:
        if table in tables:
            cols = [c["name"] for c in inspector.get_columns(table)]
            if column not in cols:
                sync_conn.execute(
                    sql_text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
                )

    ensure_column("employees", "employee_code", "VARCHAR(6)")  # v0.2
    ensure_column("ai_settings", "assign_prompt", "TEXT")  # v0.4
    ensure_column("users", "token_version", "INTEGER NOT NULL DEFAULT 0")  # v0.6
    # v0.5 — partner törzsadat-bővítés
    ensure_column("partners", "partner_type", "VARCHAR(16) NOT NULL DEFAULT 'customer'")
    ensure_column("partners", "tax_number", "VARCHAR(32)")
    ensure_column("partners", "eu_tax_number", "VARCHAR(32)")
    ensure_column("partners", "reg_number", "VARCHAR(64)")
    ensure_column("partners", "website", "VARCHAR(256)")
    ensure_column("partners", "billing_address", "VARCHAR(512)")
    ensure_column("partners", "bank_account", "VARCHAR(64)")
    ensure_column("partners", "payment_terms_days", "INTEGER")
    # v0.7 — strukturált címek + ügyfél-azonosító
    ensure_column("partners", "partner_code", "VARCHAR(16)")
    ensure_column("partners", "address_zip", "VARCHAR(16)")
    ensure_column("partners", "address_city", "VARCHAR(128)")
    ensure_column("partners", "address_street", "VARCHAR(256)")
    ensure_column("partners", "address_number", "VARCHAR(32)")
    ensure_column("partners", "billing_zip", "VARCHAR(16)")
    ensure_column("partners", "billing_city", "VARCHAR(128)")
    ensure_column("partners", "billing_street", "VARCHAR(256)")
    ensure_column("partners", "billing_number", "VARCHAR(32)")
    if "partners" in tables:
        sync_conn.execute(
            sql_text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_partners_code ON partners (partner_code)"
            )
        )
    # v0.9 — új szerepkörök (uzletkoto, szervizes): a régi CHECK-korlát cseréje
    # (PostgreSQL; SQLite-on a friss create_all már az új korláttal készül)
    if sync_conn.dialect.name == "postgresql" and "users" in tables:
        sync_conn.execute(sql_text("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role"))
        sync_conn.execute(
            sql_text(
                "ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK "
                "(role IN ('admin','manager','uzletkoto','szervizes','employee'))"
            )
        )

    # v0.10 — elszámolási bizonylat: partner-aláírás + email-küldés időpontja
    ensure_column("settlements", "partner_signature", "TEXT")
    ensure_column("settlements", "receipt_sent_at", "TIMESTAMPTZ" if sync_conn.dialect.name == "postgresql" else "TIMESTAMP")

    # v0.11 — kintlévőség-követés: fizetési státusz + határidő + fizetve-időpont.
    # Meglévő számlázott sorok backfillje: készpénz/kártya = fizetve, utalás = kint.
    ensure_column("settlements", "payment_status", "VARCHAR(16) NOT NULL DEFAULT 'none'")
    ensure_column("settlements", "due_date", "DATE")
    ensure_column("settlements", "paid_at", "TIMESTAMPTZ" if sync_conn.dialect.name == "postgresql" else "TIMESTAMP")
    if "settlements" in tables:
        sync_conn.execute(
            sql_text(
                "UPDATE settlements SET payment_status = CASE "
                "WHEN payment_method IN ('cash','card') THEN 'paid' ELSE 'outstanding' END "
                "WHERE invoiced AND payment_status = 'none'"
            )
        )

    # v0.12 — alacsony készlet riasztási küszöb a termékeken
    ensure_column("products", "low_stock_threshold", "FLOAT")

    # v0.13 — partner-portál token
    ensure_column("partners", "portal_token", "VARCHAR(64)")
    if "partners" in tables:
        sync_conn.execute(
            sql_text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_partners_portal_token "
                "ON partners (portal_token)"
            )
        )

    # v0.15 — automatikus tudásbázis-bővítés kapcsolója
    ensure_column("support_settings", "auto_kb", "BOOLEAN NOT NULL DEFAULT TRUE")

    # v0.18 — szerződés-mezők a gépeken + szerviz-költség a jegyeken
    ensure_column("assets", "contract_min_portions", "INTEGER")
    ensure_column("assets", "contract_below_min_price", "FLOAT")
    ensure_column("assets", "rent_fee", "FLOAT")
    ensure_column("service_tickets", "parts", "JSONB" if sync_conn.dialect.name == "postgresql" else "JSON")
    ensure_column("service_tickets", "labor_fee", "FLOAT")

    # v0.17 — két számlázó cég: a partner szerződött cége (xp = X-Presso
    # Coffee Kft., pc = Premium Caffe Kft.), pillanatkép az elszámoláson,
    # második Billingó-fiók a Premium Caffe-nak.
    ensure_column("partners", "invoicing_company", "VARCHAR(8)")
    ensure_column("settlements", "invoicing_company", "VARCHAR(8)")
    _blob = "BYTEA" if sync_conn.dialect.name == "postgresql" else "BLOB"
    ensure_column("billingo_settings", "pc_api_key_encrypted", _blob)
    ensure_column("billingo_settings", "pc_block_id", "INTEGER")
    ensure_column("billingo_settings", "pc_test_mode", "BOOLEAN NOT NULL DEFAULT TRUE")

    # v0.16 — ügyfél saját gépe (nem kihelyezett saját eszköz) + hivatalos
    # cégnév a partnereken. Backfill az Xpresso-importból: a gép-notes
    # "Xpresso hely: Ügyfél..." jelölése alapján.
    ensure_column("assets", "customer_owned", "BOOLEAN NOT NULL DEFAULT FALSE")
    ensure_column("partners", "company_name", "VARCHAR(256)")
    if "assets" in tables:
        false_lit = "FALSE" if sync_conn.dialect.name == "postgresql" else "0"
        true_lit = "TRUE" if sync_conn.dialect.name == "postgresql" else "1"
        sync_conn.execute(
            sql_text(
                f"UPDATE assets SET customer_owned = {true_lit} "
                f"WHERE customer_owned = {false_lit} "
                "AND notes LIKE '%Xpresso hely: Ügyfél%'"
            )
        )

    # v0.14 — gép QR-token a nyilvános támogatási oldalhoz
    ensure_column("assets", "qr_token", "VARCHAR(64)")
    if "assets" in tables:
        sync_conn.execute(
            sql_text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_assets_qr_token ON assets (qr_token)"
            )
        )

    # v0.8 — gépek (assets) törzsadat-bővítés
    ensure_column("assets", "manufacturer", "VARCHAR(128)")
    ensure_column("assets", "article_number", "VARCHAR(64)")
    ensure_column("assets", "location_type", "VARCHAR(64)")
    ensure_column("assets", "counter", "INTEGER")
    ensure_column("assets", "norm", "FLOAT")
    ensure_column("assets", "tangible", "BOOLEAN NOT NULL DEFAULT FALSE")
    if "employees" in tables:
        sync_conn.execute(
            sql_text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_code ON employees (employee_code)"
            )
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # v1 schema management: tables are created on boot (idempotent), plus
    # small targeted column migrations below for already-deployed databases.
    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_run_alembic)
    except Exception:
        # Átmeneti védőháló: ha az Alembic elhasal, a régi ensure_column +
        # create_all út továbbra is bootolható állapotba hozza az appot.
        logger.exception("Alembic migration failed — legacy schema path still runs")
    async with engine.begin() as conn:
        await conn.run_sync(_ensure_employee_code_column)
        await conn.run_sync(Base.metadata.create_all)

    # Meglévő dolgozók törzsszám- és partnerek kód-backfillje (idempotens).
    from app.db import get_session_factory
    from app.services.wfm.codes import backfill_employee_codes, backfill_partner_codes

    async with get_session_factory()() as session:
        filled = await backfill_employee_codes(session)
        if filled:
            logger.info("Backfilled %d employee codes", filled)
        filled_partners = await backfill_partner_codes(session)
        if filled_partners:
            logger.info("Backfilled %d partner codes", filled_partners)

        # v0.16 backfill: az Xpresso-importnál a hivatalos cégnév a notes
        # "Cégnév: X" sorába került — átemeljük a company_name mezőbe, és a
        # sort kivesszük a megjegyzésből (idempotens: utána nincs találat).
        from sqlalchemy import select as sa_select

        from app.models import Partner

        rows = (
            (
                await session.execute(
                    sa_select(Partner).where(
                        Partner.company_name.is_(None),
                        Partner.notes.like("%Cégnév: %"),
                    )
                )
            )
            .scalars()
            .all()
        )
        for p in rows:
            lines = (p.notes or "").splitlines()
            kept: list[str] = []
            for line in lines:
                if p.company_name is None and line.strip().startswith("Cégnév: "):
                    p.company_name = line.strip()[len("Cégnév: "):][:256] or None
                else:
                    kept.append(line)
            p.notes = "\n".join(kept).strip() or None
        if rows:
            await session.commit()
            logger.info("Backfilled company_name for %d partners", len(rows))

        # v0.18 backfill: az Xpresso-importnál a szerződéses feltételek a gép
        # notes "Szerződés (import): ..." sorába kerültek — mezőkbe emeljük.
        # A régi árak bruttók voltak → nettó = /1.27 (az eredeti sor megmarad).
        import re as _re

        from app.models import Asset as _Asset

        contract_rows = (
            (
                await session.execute(
                    sa_select(_Asset).where(
                        _Asset.rent_fee.is_(None),
                        _Asset.contract_min_portions.is_(None),
                        _Asset.notes.like("%Szerződés (import):%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        filled_contracts = 0
        for a in contract_rows:
            line = next(
                (ln for ln in (a.notes or "").splitlines() if "Szerződés (import):" in ln), ""
            )
            m_min = _re.search(r"min\.\s*(\d+)\s*adag", line)
            m_below = _re.search(r"min\. alatti adagár\s*([\d\s]+)\s*Ft", line)
            m_rent = _re.search(r"bérleti díj\s*([\d\s]+)\s*Ft", line)
            changed = False
            if m_min:
                a.contract_min_portions = int(m_min.group(1))
                changed = True
            if m_below:
                a.contract_below_min_price = round(
                    float(m_below.group(1).replace(" ", "")) / 1.27, 2
                )
                changed = True
            if m_rent:
                a.rent_fee = round(float(m_rent.group(1).replace(" ", "")) / 1.27, 2)
                changed = True
            if changed:
                filled_contracts += 1
        if filled_contracts:
            await session.commit()
            logger.info("Backfilled contract terms for %d assets", filled_contracts)

    # Értesítési háttérhurok (napi összefoglaló + heti mentés) — tesztben nem fut.
    import asyncio as _asyncio
    import os as _os

    notify_task = None
    if not _os.getenv("WFM_DISABLE_SCHEDULER"):
        from app.services.wfm.notifier import notification_loop

        notify_task = _asyncio.create_task(notification_loop())
    yield
    if notify_task is not None:
        notify_task.cancel()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Iwfm API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(employees.router, prefix="/api/employees", tags=["employees"])
    app.include_router(shifts.router, prefix="/api/shifts", tags=["shifts"])
    app.include_router(timeoff.router, prefix="/api/time-off", tags=["time-off"])
    app.include_router(timeclock.router, prefix="/api/time-entries", tags=["time-entries"])
    app.include_router(payroll.router, prefix="/api/payroll", tags=["payroll"])
    app.include_router(me.router, prefix="/api/me", tags=["self-service"])
    app.include_router(kiosk.router, prefix="/api/kiosk", tags=["kiosk"])
    app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
    app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])
    app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
    app.include_router(tasks.me_router, prefix="/api/me/tasks", tags=["self-service"])
    app.include_router(inventory.router, prefix="/api/partners", tags=["partners"])
    app.include_router(inventory.assets_router, prefix="/api/assets", tags=["assets"])
    app.include_router(consignment.products_router, prefix="/api/products", tags=["products"])
    app.include_router(consignment.stock_router, prefix="/api/partners", tags=["partner-stock"])
    app.include_router(consignment.settlements_router, prefix="/api/settlements", tags=["settlements"])
    app.include_router(service.router, prefix="/api/service", tags=["service"])
    app.include_router(audit.router, prefix="/api/audit", tags=["audit"])
    app.include_router(portal.router, prefix="/api/portal", tags=["portal"])
    app.include_router(portal.manage_router, prefix="/api/partners", tags=["portal"])
    app.include_router(support.labels_router, prefix="/api/assets", tags=["support"])
    app.include_router(support.public_router, prefix="/api/support", tags=["support"])
    app.include_router(knowledge.router, prefix="/api/knowledge", tags=["knowledge"])
    app.include_router(orders.router, prefix="/api/orders", tags=["orders"])
    app.include_router(import_export.router, prefix="/api/import-export", tags=["import-export"])
    app.include_router(geo.router, prefix="/api/geo", tags=["geo"])
    app.include_router(assistant_api.router, prefix="/api/assistant", tags=["assistant"])
    app.include_router(admin_tools.router, prefix="/api/admin", tags=["admin"])
    app.include_router(automation_api.router)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "app": settings.app_name}

    return app


app = create_app()
