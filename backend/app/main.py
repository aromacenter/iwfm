"""Iwfm backend — FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    auth,
    consignment,
    dashboard,
    employees,
    import_export,
    inventory,
    kiosk,
    me,
    payroll,
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
    yield
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
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
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
    app.include_router(import_export.router, prefix="/api/import-export", tags=["import-export"])

    @app.get("/api/health")
    async def health():
        return {"ok": True, "app": settings.app_name}

    return app


app = create_app()
