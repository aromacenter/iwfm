"""Előfizetési licenc: sávok, fiók- és dolgozó-limitek, lejárat-kezelés.

A licenc egyetlen sor a license_settings táblában. Sor NÉLKÜL a rendszer
korlátlan (a saját példányunk); bérelt példányon az üzemeltető állítja be a
sávot a Beállításokban. Lejárat után türelmi idő jár (GRACE_DAYS), utána a
rendszer csak-olvasásra vált — adat nem vész el, csak az írás áll meg.
"""

from __future__ import annotations

import time as _time
from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, LicenseSettings, User

# Sávok: fiók = belépős felhasználó, dolgozó = aktív munkavállalói törzsadat.
PLANS: dict[str, dict] = {
    "s": {"max_users": 2, "max_employees": 5},
    "m": {"max_users": 5, "max_employees": 15},
    "l": {"max_users": 12, "max_employees": 40},
    "xl": {"max_users": None, "max_employees": None},
}
GRACE_DAYS = 14

# A lejárat-középréteg minden írásnál fut — rövid ideig gyorsítótárazunk,
# hogy ne menjen kérésenként adatbázis-lekérdezés.
_CACHE_TTL = 60.0
_cache: dict = {"at": 0.0, "value": None}


def invalidate_cache() -> None:
    _cache["at"] = 0.0
    _cache["value"] = None


async def get_license_row(db: AsyncSession) -> LicenseSettings | None:
    return (
        await db.execute(select(LicenseSettings).where(LicenseSettings.id == 1))
    ).scalar_one_or_none()


def limits_for(row: LicenseSettings | None) -> dict:
    """A hatályos limitek: sáv + esetleges egyedi felülírás."""
    if row is None:
        return {"plan": "xl", "max_users": None, "max_employees": None}
    base = PLANS.get(row.plan, PLANS["xl"])
    return {
        "plan": row.plan if row.plan in PLANS else "xl",
        "max_users": row.max_users_override
        if row.max_users_override is not None else base["max_users"],
        "max_employees": row.max_employees_override
        if row.max_employees_override is not None else base["max_employees"],
    }


def license_state(row: LicenseSettings | None, today: date | None = None) -> dict:
    """ok | grace | expired — és hány nap van hátra (a türelmi időből is)."""
    today = today or date.today()
    if row is None or row.valid_until is None:
        return {"state": "ok", "days_left": None, "grace_days_left": None}
    days = (row.valid_until - today).days
    if days >= 0:
        return {"state": "ok", "days_left": days, "grace_days_left": None}
    grace_left = GRACE_DAYS + days  # days negatív
    if grace_left >= 0:
        return {"state": "grace", "days_left": days, "grace_days_left": grace_left}
    return {"state": "expired", "days_left": days, "grace_days_left": 0}


async def usage(db: AsyncSession) -> dict:
    users = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    employees = (
        await db.execute(
            select(func.count()).select_from(Employee).where(Employee.status == "active")
        )
    ).scalar_one()
    return {"users": int(users), "employees": int(employees)}


async def check_capacity(db: AsyncSession, *, new_users: int = 0, new_employees: int = 0) -> None:
    """422, ha az új fiók/dolgozó már nem fér a licencbe."""
    row = await get_license_row(db)
    lim = limits_for(row)
    if lim["max_users"] is None and lim["max_employees"] is None:
        return
    used = await usage(db)
    if lim["max_users"] is not None and used["users"] + new_users > lim["max_users"]:
        raise HTTPException(
            status_code=422,
            detail={"code": "license.user_limit", "limit": lim["max_users"]},
        )
    if (
        lim["max_employees"] is not None
        and used["employees"] + new_employees > lim["max_employees"]
    ):
        raise HTTPException(
            status_code=422,
            detail={"code": "license.employee_limit", "limit": lim["max_employees"]},
        )


async def expired_now(session_factory) -> bool:
    """A lejárat-középréteg gyors kérdése (gyorsítótárral): írás-tiltás kell-e."""
    now = _time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < _CACHE_TTL:
        return _cache["value"]
    async with session_factory() as db:
        row = await get_license_row(db)
    value = license_state(row)["state"] == "expired"
    _cache["at"] = now
    _cache["value"] = value
    return value
