"""Blokkoló-terminál (kiosk) — bejelentkezés nélküli be-/kiblokkolás törzsszámmal.

Falra szerelt / pultra tett közös eszközön fut: a dolgozó beüti a 6 jegyű
törzsszámát, a rendszer vált (nyitott bejegyzés → kiblokkolás, egyébként
beblokkolás). Védelem: per-IP próbálkozás-korlát + egységes hibaüzenet
(a kód létezéséről nem ad ki információt).
"""

from __future__ import annotations

import time as _time
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit
from app.api.timeclock import worked_minutes
from app.db import get_db
from app.models import Employee, TimeEntry

router = APIRouter()

_MAX_ATTEMPTS = 10
_WINDOW_SECONDS = 5 * 60
# IP-től független, globális korlát: hamisított X-Forwarded-For fejléccel
# (IP-váltogatással) sem lehet végigpróbálgatni a 6 jegyű kódteret.
_GLOBAL_MAX = 30
_GLOBAL_WINDOW = 15 * 60
_GLOBAL_KEY = "\x00global"
_failed: dict[str, list[float]] = defaultdict(list)


def _throttled(key: str, limit: int = _MAX_ATTEMPTS, window: int = _WINDOW_SECONDS) -> bool:
    now = _time.monotonic()
    attempts = [t for t in _failed[key] if now - t < window]
    _failed[key] = attempts
    return len(attempts) >= limit


class KioskClockBody(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    # 160. kör: a dolgozó aláírása (PNG data URL), ha a cég kéri a kioszkon
    signature: str | None = Field(default=None, max_length=120_000)


class KioskClockOut(BaseModel):
    action: str  # 'in' | 'out'
    employee_name: str
    at: datetime
    worked_minutes: int | None = None  # kiblokkolásnál: ledolgozott idő


class KioskPreviewOut(BaseModel):
    action: str  # 'in' | 'out' — mi következne
    employee_name: str
    signature_required: bool


async def _signature_required(db: AsyncSession) -> bool:
    from app.models import WorkRulesSettings

    row = (await db.execute(select(WorkRulesSettings).where(WorkRulesSettings.id == 1))).scalar_one_or_none()
    return bool(row and row.kiosk_signature)


async def _lookup(db: AsyncSession, request: Request, code: str):
    ip = request.client.host if request.client else "?"
    if _throttled(ip) or _throttled(_GLOBAL_KEY, _GLOBAL_MAX, _GLOBAL_WINDOW):
        raise HTTPException(status_code=429, detail={"code": "kiosk.rate_limited"})
    emp = (
        await db.execute(
            select(Employee).where(Employee.employee_code == code, Employee.status == "active")
        )
    ).scalar_one_or_none()
    if emp is None:
        _failed[ip].append(_time.monotonic())
        _failed[_GLOBAL_KEY].append(_time.monotonic())
        raise HTTPException(status_code=404, detail={"code": "kiosk.unknown_code"})
    _failed.pop(ip, None)
    open_entry = (
        await db.execute(
            select(TimeEntry)
            .where(TimeEntry.employee_id == emp.id, TimeEntry.clock_out.is_(None))
            .order_by(TimeEntry.clock_in.desc())
        )
    ).scalars().first()
    return emp, open_entry


@router.post("/preview", response_model=KioskPreviewOut)
async def kiosk_preview(body: KioskClockBody, request: Request, db: AsyncSession = Depends(get_db)):
    """A kód beütése után: be- vagy kiblokkolás jönne, és kell-e hozzá aláírás
    (160. kör) — a terminál ez alapján mutatja az aláírás-mezőt."""
    emp, open_entry = await _lookup(db, request, body.code)
    return KioskPreviewOut(
        action="out" if open_entry is not None else "in",
        employee_name=f"{emp.last_name} {emp.first_name}",
        signature_required=await _signature_required(db),
    )


@router.post("/clock", response_model=KioskClockOut)
async def kiosk_clock(
    body: KioskClockBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    emp, open_entry = await _lookup(db, request, body.code)
    signature = (body.signature or "").strip() or None
    if signature and not signature.startswith("data:image/png;base64,"):
        raise HTTPException(status_code=422, detail={"code": "kiosk.bad_signature"})
    if await _signature_required(db) and not signature:
        raise HTTPException(status_code=422, detail={"code": "kiosk.signature_required"})
    now = datetime.now(UTC)

    name = f"{emp.last_name} {emp.first_name}"
    if open_entry is not None:
        open_entry.clock_out = now
        if signature:
            open_entry.sign_out_data = signature
        await record_audit(
            db, actor=None, action="timeclock.kiosk_out", entity_type="time_entry",
            entity_id=str(open_entry.id), request=request,
        )
        await db.commit()
        return KioskClockOut(
            action="out", employee_name=name, at=now, worked_minutes=worked_minutes(open_entry)
        )

    entry = TimeEntry(employee_id=emp.id, clock_in=now, source="self", sign_in_data=signature)
    db.add(entry)
    await db.flush()
    await record_audit(
        db, actor=None, action="timeclock.kiosk_in", entity_type="time_entry",
        entity_id=str(entry.id), request=request,
    )
    await db.commit()
    return KioskClockOut(action="in", employee_name=name, at=now)
