"""Iwfm backend — FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    admin_tools,
    assistant as assistant_api,
    audit,
    auth,
    automation as automation_api,
    consignment,
    contracts as contracts_api,
    dashboard,
    delivery as delivery_api,
    employees,
    geo,
    gls as gls_api,
    import_export,
    intake as intake_api,
    inventory,
    kiosk,
    knowledge,
    me,
    operator as operator_api,
    orders,
    payroll,
    portal,
    print_jobs as print_jobs_api,
    quotes as quotes_api,
    service,
    support,
    settings as settings_api,
    shifts,
    tasks,
    timeclock,
    timeoff,
    warehouse,
)
from app.core.config import get_settings
from app.db import get_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


BASELINE_REVISION = "1761410c3a1e"  # a v0.19-es Alembic-átálláskori teljes séma


def _run_alembic(sync_conn) -> None:
    """Sémaverziózás Alembickel (v0.19-től) — ez az EGYETLEN séma-út.

    - Már élő, Alembic előtti adatbázis (van users tábla, nincs alembic_version):
      bélyegzés (stamp) DDL nélkül, majd upgrade. ÁTMENETI ág — a bélyegzés
      célpontját a séma állapotából ismerjük fel (marker: az első baseline
      utáni migráció egy oszlopa), hogy a stamp ne ugorjon át valós migrációt.
    - Friss vagy már verziózott adatbázis: upgrade head.
    Hiba esetén a boot megáll (nem indulunk el rossz sémával).
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Séma: kizárólag Alembic-migrációk (v0.24-től a legacy ensure_column +
    # create_all út és az egyszeri import-backfillek kivezetve). Ha a migráció
    # elhasal, a boot megáll — nem futunk rossz sémán.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(_run_alembic)

    # Dolgozói törzsszám + partner-kód pótlása kód nélkül létrejött sorokra
    # (pl. import) — gyors, indexelt, idempotens.
    from app.db import get_session_factory
    from app.services.wfm.codes import backfill_employee_codes, backfill_partner_codes

    async with get_session_factory()() as session:
        filled = await backfill_employee_codes(session)
        if filled:
            logger.info("Backfilled %d employee codes", filled)
        filled_partners = await backfill_partner_codes(session)
        if filled_partners:
            logger.info("Backfilled %d partner codes", filled_partners)

    # Nyomtató-ügynök kulcs bootstrapelése környezeti változóból: ha a
    # WFM_PRINT_AGENT_KEY be van állítva, induláskor bekerül a
    # print_settings-be. Így a kulcs kiadható úgy is, hogy a felületen
    # (vagy bárhol máshol) sosem jelenik meg.
    import os as _os_boot

    boot_key = _os_boot.getenv("WFM_PRINT_AGENT_KEY")
    if boot_key:
        from sqlalchemy import select as _select

        from app.models import PrintSettings

        async with get_session_factory()() as session:
            row = (
                await session.execute(
                    _select(PrintSettings).where(PrintSettings.id == 1)
                )
            ).scalar_one_or_none()
            if row is None:
                row = PrintSettings(id=1)
                session.add(row)
            if row.agent_key != boot_key:
                row.agent_key = boot_key
                await session.commit()
                logger.info("Print agent key installed from WFM_PRINT_AGENT_KEY")

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

    # Lejárt licenc (türelmi időn is túl): csak-olvasás mód. Adat nem vész
    # el, a GET-ek és a belépés működnek — az írás áll meg, a licenc-oldal
    # kivételével, hogy a hosszabbítást be lehessen állítani.
    _license_write_allow = (
        "/api/auth/", "/api/settings/license", "/api/health", "/api/operator",
    )

    @app.middleware("http")
    async def license_guard(request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith(
            "/api"
        ) and not any(request.url.path.startswith(p) for p in _license_write_allow):
            from app.db import get_session_factory
            from app.services.wfm.license import expired_now

            if await expired_now(get_session_factory()):
                return JSONResponse(
                    status_code=423, content={"detail": {"code": "license.expired"}}
                )
        return await call_next(request)

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
    app.include_router(operator_api.router, prefix="/api/operator", tags=["operator"])
    app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
    app.include_router(tasks.me_router, prefix="/api/me/tasks", tags=["self-service"])
    app.include_router(
        tasks.quote_public_router, prefix="/api/public/worksheet-quote", tags=["public"]
    )
    app.include_router(inventory.router, prefix="/api/partners", tags=["partners"])
    app.include_router(contracts_api.router, prefix="/api/partners", tags=["contracts"])
    app.include_router(contracts_api.overview_router, prefix="/api/contracts", tags=["contracts"])
    app.include_router(inventory.assets_router, prefix="/api/assets", tags=["assets"])
    app.include_router(consignment.products_router, prefix="/api/products", tags=["products"])
    app.include_router(consignment.stock_router, prefix="/api/partners", tags=["partner-stock"])
    app.include_router(consignment.settlements_router, prefix="/api/settlements", tags=["settlements"])
    app.include_router(service.router, prefix="/api/service", tags=["service"])
    app.include_router(intake_api.router, prefix="/api/intakes", tags=["intakes"])
    app.include_router(audit.router, prefix="/api/audit", tags=["audit"])
    # Kapcsolható extra modulok: kikapcsolva a teljes router zárva (403),
    # nem csak a menü tűnik el. NULL modullista = minden megy (X-Presso).
    from app.services.wfm.license import require_module as _mod

    app.include_router(
        portal.router, prefix="/api/portal", tags=["portal"],
        dependencies=[Depends(_mod("portal"))],
    )
    app.include_router(
        portal.manage_router, prefix="/api/partners", tags=["portal"],
        dependencies=[Depends(_mod("portal"))],
    )
    app.include_router(support.labels_router, prefix="/api/assets", tags=["support"])
    app.include_router(
        print_jobs_api.router, prefix="/api/print-jobs", tags=["print"],
        dependencies=[Depends(_mod("labels"))],
    )
    app.include_router(
        gls_api.router, prefix="/api/gls", tags=["gls"],
        dependencies=[Depends(_mod("gls"))],
    )
    app.include_router(
        print_jobs_api.agent_router, prefix="/api/print-agent", tags=["print"],
        dependencies=[Depends(_mod("labels"))],
    )
    app.include_router(
        support.public_router, prefix="/api/support", tags=["support"],
        dependencies=[Depends(_mod("support"))],
    )
    app.include_router(
        knowledge.router, prefix="/api/knowledge", tags=["knowledge"],
        dependencies=[Depends(_mod("support"))],
    )
    app.include_router(orders.router, prefix="/api/orders", tags=["orders"])
    app.include_router(import_export.router, prefix="/api/import-export", tags=["import-export"])
    app.include_router(geo.router, prefix="/api/geo", tags=["geo"])
    app.include_router(warehouse.router, prefix="/api/warehouses", tags=["warehouses"])
    app.include_router(delivery_api.router, prefix="/api/deliveries", tags=["deliveries"])
    app.include_router(quotes_api.router, prefix="/api/quotes", tags=["quotes"])
    app.include_router(quotes_api.public_router, prefix="/api/public", tags=["quotes"])
    app.include_router(warehouse.po_router, prefix="/api/purchase-orders", tags=["purchase-orders"])
    app.include_router(
        assistant_api.router, prefix="/api/assistant", tags=["assistant"],
        dependencies=[Depends(_mod("ai"))],
    )
    app.include_router(admin_tools.router, prefix="/api/admin", tags=["admin"])
    app.include_router(automation_api.router)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "app": settings.app_name}

    return app


app = create_app()
