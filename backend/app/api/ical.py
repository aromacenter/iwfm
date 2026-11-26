"""Naptár-előfizetés (189. kör): minden dolgozó privát iCal-linkje.

A telefon/Google/Apple naptára a linkre feliratkozva látja a saját feladatait
(egész napos, a határidőn), a publikált műszakokat (időpontos) és a jóváhagyott
távolléteket — az app megnyitása nélkül, óránként frissülve. A link tokenes,
belépés nélkül olvasható; új link kérhető, a régi azonnal érvénytelen.
"""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, record_audit
from app.db import get_db
from app.models import Employee, Shift, Task, TimeOffRequest, User

router = APIRouter()  # /api/me/ical
public_router = APIRouter()  # /api/public/ical

PAST_DAYS = 30
FUTURE_DAYS = 90
TIMEOFF_LABELS = {"annual": "Szabadság", "sick": "Betegszabadság", "unpaid": "Fizetés nélküli", "other": "Távollét"}


def _esc(s: str | None) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: 75 oktettenként sortörés + szóköz."""
    out, raw = [], line.encode("utf-8")
    while len(raw) > 73:
        cut = 73
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = b" " + raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n".join(out)


def _event(uid: str, summary: str, *, start: date | datetime, end: date | datetime, description: str = "", location: str = "", status: str = "CONFIRMED") -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"]
    if isinstance(start, datetime):
        lines.append(f"DTSTART:{start.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTEND:{end.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    else:
        lines.append(f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}")
        lines.append(f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}")
    lines.append(f"SUMMARY:{_esc(summary)}")
    if description:
        lines.append(f"DESCRIPTION:{_esc(description)}")
    if location:
        lines.append(f"LOCATION:{_esc(location)}")
    lines.append(f"STATUS:{status}")
    lines.append("END:VEVENT")
    return lines


async def build_ics(db: AsyncSession, user: User, emp: Employee | None, today: date | None = None) -> str:
    from zoneinfo import ZoneInfo

    from app.services.wfm.links import frontend_url

    tz = ZoneInfo("Europe/Budapest")
    today = today or date.today()
    lo, hi = today - timedelta(days=PAST_DAYS), today + timedelta(days=FUTURE_DAYS)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//X-admin//Naptar//HU", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc('X-admin – ' + user.display_name)}", "X-WR-TIMEZONE:Europe/Budapest",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H",
    ]
    if emp is not None:
        tasks = (
            await db.execute(select(Task).where(Task.employee_id == emp.id, Task.due_date >= lo, Task.due_date <= hi).order_by(Task.due_date))
        ).scalars().all()
        for t in tasks:
            done = t.status == "done"
            summary = ("✅ " if done else "🗂️ ") + t.title
            desc = (t.description or "")[:500]
            desc = (desc + "\n" if desc else "") + frontend_url(f"/feladataim?task={t.id}")
            lines += _event(f"task-{t.id}@x-admin", summary, start=t.due_date, end=t.due_date + timedelta(days=1),
                            description=desc, status="CONFIRMED" if not done else "CONFIRMED")
        shifts = (
            await db.execute(select(Shift).where(Shift.employee_id == emp.id, Shift.status == "published", Shift.work_date >= lo, Shift.work_date <= hi))
        ).scalars().all()
        for s in shifts:
            start = datetime.combine(s.work_date, s.start_time, tzinfo=tz)
            end = datetime.combine(s.work_date, s.end_time, tzinfo=tz)
            if end <= start:
                end += timedelta(days=1)
            summary = "🗓️ Műszak" + (f" · {s.role_label}" if s.role_label else "")
            lines += _event(f"shift-{s.id}@x-admin", summary, start=start, end=end, description=s.note or "", location=s.location or "")
        offs = (
            await db.execute(select(TimeOffRequest).where(TimeOffRequest.employee_id == emp.id, TimeOffRequest.status == "approved",
                                                         TimeOffRequest.end_date >= lo, TimeOffRequest.start_date <= hi))
        ).scalars().all()
        for o in offs:
            label = TIMEOFF_LABELS.get(o.type or "", "Távollét")
            lines += _event(f"timeoff-{o.id}@x-admin", f"🏖️ {label}", start=o.start_date, end=o.end_date + timedelta(days=1), description=o.reason or "")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def _url(user: User) -> str:
    from app.core.config import get_settings

    base = get_settings().frontend_origin.rstrip("/")
    return f"{base}/api/public/ical/{user.ical_token}.ics"


@router.get("/ical")
async def my_ical(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """A saját előfizetési link (első híváskor készül a token)."""
    if not user.ical_token:
        user.ical_token = secrets.token_urlsafe(24)
        await db.commit()
    return {"url": _url(user)}


@router.post("/ical/rotate")
async def rotate_ical(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Új link — a régi azonnal érvénytelen."""
    user.ical_token = secrets.token_urlsafe(24)
    await record_audit(db, actor=user, action="ical.rotate", entity_type="user", entity_id=str(user.id), request=request)
    await db.commit()
    return {"url": _url(user)}


@public_router.get("/{token}.ics")
async def public_ical(token: str, db: AsyncSession = Depends(get_db)):
    if not token or len(token) > 64:
        raise HTTPException(status_code=404, detail={"code": "ical.not_found"})
    user = (await db.execute(select(User).where(User.ical_token == token))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail={"code": "ical.not_found"})
    emp = (await db.execute(select(Employee).where(Employee.user_id == user.id))).scalar_one_or_none()
    body = await build_ics(db, user, emp)
    return Response(content=body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="x-admin.ics"', "Cache-Control": "private, max-age=900"})
