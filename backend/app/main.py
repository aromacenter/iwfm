"""Iwfm backend — FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, employees, me, payroll, shifts, timeclock, timeoff
from app.core.config import get_settings
from app.db import get_engine
from app.models import Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # v1 schema management: tables are created on boot (idempotent). Alembic
    # gets introduced with the first schema *change* — for the initial release
    # create_all is the entire migration story, locally and on Railway alike.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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

    @app.get("/api/health")
    async def health():
        return {"ok": True, "app": settings.app_name}

    return app


app = create_app()
