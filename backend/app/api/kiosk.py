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
_failed: dict[str, list[float]] = defaultdict(list)


def _throttled(ip: str) -> bool:
    now = _time.monotonic()
    attempts = [t for t in _failed[ip] if now - t < _WINDOW_SECONDS]
    _failed[ip] = attempts
    return len(attempts) >= _MAX_ATTEMPTS


class KioskClockBody(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class KioskClockOut(BaseModel):
    action: str  # 'in' | 'out'
    employee_name: str
    at: datetime
    worked_minutes: int | None = None  # kiblokkolásnál: ledolgozott idő


@router.post("/clock", response_model=KioskClockOut)
async def kiosk_clock(
    body: KioskClockBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else "?"
    if _throttled(ip):
        raise HTTPException(status_code=429, detail={"code": "kiosk.rate_limited"})

    emp = (
        await db.execute(
            select(Employee).where(
                Employee.employee_code == body.code, Employee.status == "active"
            )
        )
    ).scalar_one_or_none()
    if emp is None:
        _failed[ip].append(_time.monotonic())
        raise HTTPException(status_code=404, detail={"code": "kiosk.unknown_code"})
    _failed.pop(ip, None)

    now = datetime.now(UTC)
    open_entry = (
        (
            await db.execute(
                select(TimeEntry)
                .where(TimeEntry.employee_id == emp.id, TimeEntry.clock_out.is_(None))
                .order_by(TimeEntry.clock_in.desc())
            )
        )
        .scalars()
        .first()
    )

    name = f"{emp.last_name} {emp.first_name}"
    if open_entry is not None:
        open_entry.clock_out = now
        await record_audit(
            db, actor=None, action="timeclock.kiosk_out", entity_type="time_entry",
            entity_id=str(open_entry.id), request=request,
        )
        await db.commit()
        return KioskClockOut(
            action="out", employee_name=name, at=now, worked_minutes=worked_minutes(open_entry)
        )

    entry = TimeEntry(employee_id=emp.id, clock_in=now, source="self")
    db.add(entry)
    await db.flush()
    await record_audit(
        db, actor=None, action="timeclock.kiosk_in", entity_type="time_entry",
        entity_id=str(entry.id), request=request,
    )
    await db.commit()
    return KioskClockOut(action="in", employee_name=name, at=now)
