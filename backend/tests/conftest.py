"""Test fixtures: real SQLite database per test + httpx ASGI client.

No mocked database — every test runs against a real engine with the real
schema (file-based SQLite in a pytest tmp dir).
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.db as app_db
from app.core.config import get_settings
from app.core.security import hash_password, mint_token
from app.models import Base, Employee, User


@pytest_asyncio.fixture()
async def client(tmp_path, monkeypatch):
    """Fresh app + fresh database for every test."""
    db_path = tmp_path / f"test_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("WFM_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("WFM_DISABLE_SCHEDULER", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    app_db.reset_engine()

    engine = app_db.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Process-szintű throttle állapotok nullázása tesztek között.
    from app.api import auth as auth_module
    from app.api import kiosk as kiosk_module

    auth_module._failed_logins.clear()
    kiosk_module._failed.clear()

    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await engine.dispose()
    get_settings.cache_clear()
    app_db.reset_engine()


async def make_user(
    *, email: str, role: str, password: str = "Passw0rd-12345", display_name: str | None = None
) -> tuple[User, dict]:
    """Insert a user directly and return (user, auth headers)."""
    factory = app_db.get_session_factory()
    async with factory() as session:
        user = User(
            email=email.lower(),
            password_hash=hash_password(password),
            display_name=display_name or email.split("@")[0],
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    headers = {"Authorization": f"Bearer {mint_token(user_id=user.id, role=user.role)}"}
    return user, headers


async def make_employee_record(user: User, **overrides) -> Employee:
    """Insert an Employee row linked to ``user``."""
    from datetime import date

    defaults = dict(
        user_id=user.id,
        last_name="Teszt",
        first_name="Elek",
        hire_date=date(2024, 1, 1),
        weekly_hours=40,
    )
    defaults.update(overrides)
    factory = app_db.get_session_factory()
    async with factory() as session:
        emp = Employee(**defaults)
        session.add(emp)
        await session.commit()
        await session.refresh(emp)
    return emp


@pytest_asyncio.fixture()
async def admin(client):
    """Admin user + headers (depends on client so the DB exists)."""
    return await make_user(email="admin@example.com", role="admin")


@pytest_asyncio.fixture()
async def manager(client):
    return await make_user(email="manager@example.com", role="manager")


@pytest_asyncio.fixture()
async def employee_user(client):
    """Employee login + linked Employee record."""
    user, headers = await make_user(email="dolgozo@example.com", role="employee")
    emp = await make_employee_record(user)
    return user, headers, emp


# ─── Valid HU identifier generators (checksum-correct test vectors) ──────────


def gen_tax_id(seed: int = 0) -> str:
    """Generate a checksum-valid adóazonosító jel."""
    base = f"8{(13371337 + seed) % 10**8:08d}"
    check = sum(int(base[i]) * (i + 1) for i in range(9)) % 11
    if check == 10:
        return gen_tax_id(seed + 1)
    return base + str(check)


def gen_taj(seed: int = 0) -> str:
    """Generate a checksum-valid TAJ szám."""
    base = f"{(36258341 + seed) % 10**8:08d}"
    check = sum(int(d) * (3 if i % 2 == 0 else 7) for i, d in enumerate(base)) % 10
    return base + str(check)


def gen_bank_account(seed: int = 0) -> str:
    """Generate a checksum-valid 16-digit pénzforgalmi jelzőszám."""
    weights = [9, 7, 3, 1]

    def fix_block(prefix7: str) -> str:
        total = sum(int(d) * weights[i % 4] for i, d in enumerate(prefix7))
        # last digit weight is weights[7 % 4] = weights[3] = 1
        last = (-total) % 10
        return prefix7 + str(last)

    block1 = fix_block(f"{(1170002 + seed) % 10**7:07d}")
    block2 = fix_block(f"{(2345678 + seed) % 10**7:07d}")
    return block1 + block2
