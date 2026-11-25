"""Telegram-összekapcsolás egyszeri kóddal (182. kör).

A félig nyilvános törzsszám helyett az appban kért, 10 percig élő, egyszer
használható 6 jegyű kód köti össze a Telegram-csevegést a fiókkal: kartonos
dolgozónál a kartonhoz (személyes feladat-értesítések + belépési kódok),
karton nélküli (admin/vezetői) fióknál a fiókhoz. A kódot a dolgozó maga kéri
(Beállítások → Kétlépcsős belépés kártya), vagy a vezető kéri le neki a
Dolgozók oldalon és átadja.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, User

CODE_TTL = timedelta(minutes=10)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def _new_code() -> str:
    return f"{secrets.randbelow(900000) + 100000:06d}"


async def issue_code_for_user(db: AsyncSession, user: User) -> dict:
    """Kód a saját fiókhoz: ha van kartonja, a kartonra (a bot a kartont köti),
    különben a fiókra."""
    emp = (await db.execute(select(Employee).where(Employee.user_id == user.id))).scalar_one_or_none()
    target = emp if emp is not None else user
    return await issue_code(db, target)


async def issue_code(db: AsyncSession, target: Employee | User) -> dict:
    code = _new_code()
    expires = datetime.now(UTC) + CODE_TTL
    target.tg_link_code = code
    target.tg_link_code_expires = expires
    await db.commit()
    return {"code": code, "expires_at": expires.isoformat(), "ttl_minutes": int(CODE_TTL.total_seconds() // 60)}


async def find_by_code(db: AsyncSession, code: str) -> Employee | User | None:
    """Élő kód → karton vagy fiók (None, ha ismeretlen vagy lejárt)."""
    now = datetime.now(UTC)
    emp = (await db.execute(select(Employee).where(Employee.tg_link_code == code))).scalar_one_or_none()
    if emp is not None:
        if _aware(emp.tg_link_code_expires) and _aware(emp.tg_link_code_expires) >= now and emp.status == "active":
            return emp
        return None
    usr = (await db.execute(select(User).where(User.tg_link_code == code))).scalar_one_or_none()
    if usr is not None and _aware(usr.tg_link_code_expires) and _aware(usr.tg_link_code_expires) >= now and usr.is_active:
        return usr
    return None


def consume_code(target: Employee | User) -> None:
    target.tg_link_code = None
    target.tg_link_code_expires = None
