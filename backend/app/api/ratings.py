"""Ügyfél-értékelés a munkalap után (186. kör).

Aláírás után (a szerelő eszközén) vagy a kiküldött e-mail linkjéről az ügyfél
1–5 csillagot és egy mondatot adhat. Publikus, tokenes végpont (a tokent a
munkalap kapja, amikor mindkét aláírás felkerül, vagy az első ügyfél-e-mailnél).
Gyenge értékelés (≤2) → azonnali privát Telegram az admin/manager dolgozóknak.
Összesítés dolgozónként a statisztikához.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import Asset, Employee, Task, User, Worksheet, WorksheetSettings

logger = logging.getLogger(__name__)
public_router = APIRouter()
router = APIRouter()

LOW_RATING = 2


class RatingBody(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)
    source: str = Field(default="link", pattern="^(device|link)$")


async def _ws_by_token(db: AsyncSession, token: str) -> Worksheet:
    if not token or len(token) > 64:
        raise HTTPException(status_code=404, detail={"code": "rating.not_found"})
    ws = (await db.execute(select(Worksheet).where(Worksheet.rating_token == token))).scalar_one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail={"code": "rating.not_found"})
    return ws


async def _context(db: AsyncSession, ws: Worksheet) -> dict:
    task = await db.get(Task, ws.task_id)
    emp = None
    if task is not None and task.employee_id is not None:
        emp = await db.get(Employee, task.employee_id)
    asset = await db.get(Asset, ws.asset_id) if ws.asset_id is not None else None
    row = (await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))).scalar_one_or_none()
    return {
        "task": task, "employee": emp, "asset": asset,
        "company": (row.company_name if row else None) or "",
        "employee_name": f"{emp.last_name} {emp.first_name}".strip() if emp else "",
    }


@public_router.get("/{token}")
async def public_rating_info(token: str, db: AsyncSession = Depends(get_db)):
    ws = await _ws_by_token(db, token)
    c = await _context(db, ws)
    return {
        "serial": ws.serial, "company_name": c["company"],
        "machine": c["asset"].name if c["asset"] else None,
        "technician": c["employee_name"] or None,
        "rated": ws.rating is not None, "rating": ws.rating,
    }


@public_router.post("/{token}")
async def public_rate(token: str, body: RatingBody, request: Request, db: AsyncSession = Depends(get_db)):
    """Egyszer értékelhető; utána 409."""
    ws = await _ws_by_token(db, token)
    if ws.rating is not None:
        raise HTTPException(status_code=409, detail={"code": "rating.already_rated"})
    ws.rating = body.rating
    ws.rating_comment = (body.comment or "").strip()[:1000] or None
    ws.rated_at = datetime.now(UTC)
    ws.rating_source = body.source
    c = await _context(db, ws)
    await record_audit(
        db, actor=None, action="worksheet.rated", entity_type="worksheet", entity_id=ws.serial,
        detail={"serial": ws.serial, "rating": body.rating, "comment": ws.rating_comment, "source": body.source,
                "employee": c["employee_name"]}, request=request,
    )
    await db.commit()

    ctx = {
        "_task_id": str(ws.task_id), "sorszam": ws.serial, "ugyfel": ws.client_name or ws.client_signer_name or "",
        "dolgozo": c["employee_name"], "ertekeles": body.rating, "megjegyzes": ws.rating_comment or "",
        "gep_nev": c["asset"].name if c["asset"] else "", "csillagok": "★" * body.rating + "☆" * (5 - body.rating),
    }
    try:
        from app.services.wfm.automation import fire_event
        from app.services.wfm.telegram import notify_event, send_personal

        fire_event("worksheet.rated", ctx)
        await notify_event(db, "worksheet.rated", ctx)
        if body.rating <= LOW_RATING:
            from app.services.wfm.links import frontend_url

            deciders = (
                await db.execute(
                    select(Employee).join(User, User.id == Employee.user_id)
                    .where(User.is_active.is_(True), User.role.in_(("admin", "manager")))
                )
            ).scalars().all()
            msg = (
                f"⚠️ Gyenge ügyfél-értékelés: {ctx['csillagok']} ({body.rating}/5) — {ws.serial}"
                + (f" · {ctx['ugyfel']}" if ctx["ugyfel"] else "") + (f" · szerelő: {ctx['dolgozo']}" if ctx["dolgozo"] else "")
                + (f"\n„{ws.rating_comment}”" if ws.rating_comment else "")
                + "\n🔗 " + frontend_url(f"/feladatok?task={ws.task_id}")
            )
            for emp in deciders:
                await send_personal(db, emp, msg)
    except Exception:  # noqa: BLE001
        logger.warning("rating notify failed", exc_info=True)
    return {"ok": True, "rating": ws.rating}


class RatingRow(BaseModel):
    serial: str
    task_id: str
    rating: int
    comment: str | None
    client: str | None
    employee: str
    rated_at: datetime | None


class EmployeeRating(BaseModel):
    name: str
    count: int
    avg: float
    low: int  # ≤2 csillag


class RatingSummary(BaseModel):
    count: int
    avg: float | None
    dist: dict[str, int]
    low: int
    by_employee: list[EmployeeRating]
    recent: list[RatingRow]


@router.get("/summary", response_model=RatingSummary)
async def rating_summary(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("agent_report")),
):
    """Értékelések a megadott időszakban (rated_at szerint): átlag, eloszlás,
    dolgozónként, legutóbbi 20 megjegyzéssel."""
    q = select(Worksheet, Task).join(Task, Task.id == Worksheet.task_id).where(Worksheet.rating.is_not(None))
    if date_from:
        q = q.where(Worksheet.rated_at >= datetime.combine(date_from, time.min, tzinfo=UTC))
    if date_to:
        q = q.where(Worksheet.rated_at <= datetime.combine(date_to, time.max, tzinfo=UTC))
    rows = (await db.execute(q.order_by(Worksheet.rated_at.desc()))).all()
    emp_ids = {t.employee_id for _, t in rows if t.employee_id is not None}
    names: dict[uuid.UUID, str] = {}
    if emp_ids:
        for e in (await db.execute(select(Employee).where(Employee.id.in_(emp_ids)))).scalars().all():
            names[e.id] = f"{e.last_name} {e.first_name}".strip()
    dist = {str(i): 0 for i in range(1, 6)}
    per: dict[str, dict] = {}
    recent: list[RatingRow] = []
    total = 0
    for ws, t in rows:
        r = int(ws.rating)
        dist[str(r)] += 1
        total += r
        name = names.get(t.employee_id, "—") if t.employee_id else "—"
        b = per.setdefault(name, {"name": name, "count": 0, "sum": 0, "low": 0})
        b["count"] += 1
        b["sum"] += r
        b["low"] += int(r <= LOW_RATING)
        if len(recent) < 20:
            recent.append(RatingRow(serial=ws.serial, task_id=str(ws.task_id), rating=r, comment=ws.rating_comment,
                                    client=ws.client_name or ws.client_signer_name, employee=name, rated_at=ws.rated_at))
    by_emp = sorted(
        (EmployeeRating(name=v["name"], count=v["count"], avg=round(v["sum"] / v["count"], 2), low=v["low"]) for v in per.values()),
        key=lambda x: (x.avg, -x.count),
    )
    return RatingSummary(
        count=len(rows), avg=round(total / len(rows), 2) if rows else None, dist=dist,
        low=sum(1 for ws, _ in rows if int(ws.rating) <= LOW_RATING), by_employee=by_emp, recent=recent,
    )

