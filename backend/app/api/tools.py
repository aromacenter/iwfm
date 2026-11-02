"""Szerszám-nyilvántartás (137. kör, ötlet #12): a szerelők szerszámai és
mérőeszközei — kinél van, milyen állapotban, mikor esedékes a következő
ellenőrzés/kalibrálás; átadás-átvétel és ellenőrzés naplózva. Külön modul
("tools" kapcsoló a licenc-rétegben). A dolgozó a saját szerszámait látja."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_own_employee, record_audit, require_any_perm, require_perm
from app.db import get_db
from app.models import Employee, Tool, ToolEvent, User

router = APIRouter()

TOOL_STATUSES = ("ok", "repair", "lost", "retired")
CHECK_SOON_DAYS = 30
MANAGE = require_any_perm("service", "tasks", "parts")


class ToolBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    serial_no: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=64)
    holder_employee_id: str | None = None
    status: str = Field(default="ok", pattern="^(ok|repair|lost|retired)$")
    next_check: date | None = None
    check_interval_months: int | None = Field(default=None, ge=1, le=120)
    note: str | None = Field(default=None, max_length=2000)


class ToolPatchBody(BaseModel):
    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=256)
    serial_no: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, pattern="^(ok|repair|lost|retired)$")
    next_check: date | None = None
    check_interval_months: int | None = Field(default=None, ge=1, le=120)
    note: str | None = Field(default=None, max_length=2000)


class HandoverBody(BaseModel):
    employee_id: str | None = None  # None = visszavétel a raktárba
    note: str | None = Field(default=None, max_length=512)


class CheckBody(BaseModel):
    next_check: date | None = None  # None = intervallumból számolva
    note: str | None = Field(default=None, max_length=512)


class ToolOut(BaseModel):
    id: str
    name: str
    serial_no: str | None
    category: str | None
    holder_employee_id: str | None
    holder_name: str | None
    status: str
    next_check: date | None
    check_interval_months: int | None
    last_check: date | None
    note: str | None
    check_state: str  # ok | soon | overdue | none
    created_at: datetime


class ToolEventOut(BaseModel):
    id: str
    kind: str
    from_name: str | None
    to_name: str | None
    note: str | None
    actor_name: str | None
    created_at: datetime


def _check_state(t: Tool, today: date) -> str:
    if t.next_check is None:
        return "none"
    if t.next_check < today:
        return "overdue"
    if t.next_check <= today + timedelta(days=CHECK_SOON_DAYS):
        return "soon"
    return "ok"


async def _names(db: AsyncSession, ids: set) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (
        await db.execute(select(Employee.id, Employee.last_name, Employee.first_name).where(Employee.id.in_(ids)))
    ).all()
    return {eid: f"{ln} {fn}" for eid, ln, fn in rows}


def _out(t: Tool, names: dict, today: date) -> ToolOut:
    return ToolOut(
        id=str(t.id), name=t.name, serial_no=t.serial_no, category=t.category,
        holder_employee_id=str(t.holder_employee_id) if t.holder_employee_id else None,
        holder_name=names.get(t.holder_employee_id), status=t.status, next_check=t.next_check,
        check_interval_months=t.check_interval_months, last_check=t.last_check, note=t.note,
        check_state=_check_state(t, today), created_at=t.created_at,
    )


async def _tool_or_404(db: AsyncSession, tool_id: str) -> Tool:
    try:
        tid = uuid.UUID(tool_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "tools.not_found"})
    t = (await db.execute(select(Tool).where(Tool.id == tid))).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "tools.not_found"})
    return t


async def _employee_or_422(db: AsyncSession, raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        eid = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "tools.bad_employee"})
    if (await db.execute(select(Employee.id).where(Employee.id == eid))).first() is None:
        raise HTTPException(status_code=422, detail={"code": "tools.bad_employee"})
    return eid


async def _serial_free(db: AsyncSession, serial: str | None, exclude: uuid.UUID | None = None) -> None:
    if not serial:
        return
    q = select(Tool.id).where(Tool.serial_no == serial)
    if exclude is not None:
        q = q.where(Tool.id != exclude)
    if (await db.execute(q)).first() is not None:
        raise HTTPException(status_code=409, detail={"code": "tools.serial_exists"})


@router.get("", response_model=list[ToolOut])
async def list_tools(
    q: str | None = Query(default=None, max_length=200),
    holder: str | None = Query(default=None),  # employee id | "none"
    status: str | None = Query(default=None),
    due: bool = Query(default=False),  # csak az ellenőrzésre esedékes / lejárt
    db: AsyncSession = Depends(get_db),
    _: User = Depends(MANAGE),
):
    query = select(Tool).order_by(Tool.name)
    if q:
        like = f"%{q}%"
        query = query.where(Tool.name.ilike(like) | Tool.serial_no.ilike(like) | Tool.category.ilike(like))
    if holder == "none":
        query = query.where(Tool.holder_employee_id.is_(None))
    elif holder:
        query = query.where(Tool.holder_employee_id == await _employee_or_422(db, holder))
    if status:
        if status not in TOOL_STATUSES:
            raise HTTPException(status_code=422, detail={"code": "tools.bad_status"})
        query = query.where(Tool.status == status)
    rows = (await db.execute(query.limit(1000))).scalars().all()
    today = date.today()
    if due:
        rows = [t for t in rows if _check_state(t, today) in ("soon", "overdue")]
    names = await _names(db, {t.holder_employee_id for t in rows})
    return [_out(t, names, today) for t in rows]


@router.get("/my", response_model=list[ToolOut])
async def my_tools(
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
):
    """A dolgozó saját szerszámai (önkiszolgáló nézet)."""
    rows = (
        await db.execute(select(Tool).where(Tool.holder_employee_id == emp.id).order_by(Tool.name))
    ).scalars().all()
    today = date.today()
    name = f"{emp.last_name} {emp.first_name}"
    return [_out(t, {emp.id: name}, today) for t in rows]


@router.post("", response_model=ToolOut, status_code=201)
async def create_tool(
    body: ToolBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(MANAGE),
):
    serial = (body.serial_no or "").strip() or None
    await _serial_free(db, serial)
    holder = await _employee_or_422(db, body.holder_employee_id)
    t = Tool(
        name=body.name.strip(), serial_no=serial, category=(body.category or "").strip() or None,
        holder_employee_id=holder, status=body.status, next_check=body.next_check,
        check_interval_months=body.check_interval_months, note=(body.note or "").strip() or None,
    )
    db.add(t)
    await db.flush()
    if holder is not None:
        db.add(ToolEvent(tool_id=t.id, kind="handover", to_employee_id=holder, actor_name=actor.display_name, note="felvétel"))
    await record_audit(
        db, actor=actor, action="tools.create", entity_type="tool", entity_id=str(t.id),
        detail={"name": t.name, "serial": serial}, request=request,
    )
    await db.commit()
    names = await _names(db, {holder})
    return _out(t, names, date.today())


@router.patch("/{tool_id}", response_model=ToolOut)
async def update_tool(
    tool_id: str,
    body: ToolPatchBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(MANAGE),
):
    t = await _tool_or_404(db, tool_id)
    data = body.model_dump(exclude_unset=True)
    if "serial_no" in data:
        data["serial_no"] = (data["serial_no"] or "").strip() or None
        await _serial_free(db, data["serial_no"], exclude=t.id)
    if "status" in data and data["status"] != t.status:
        db.add(ToolEvent(tool_id=t.id, kind="status", note=f"{t.status} → {data['status']}", actor_name=actor.display_name))
    for k, v in data.items():
        setattr(t, k, v.strip() if isinstance(v, str) and k in ("name", "category", "note") else v)
    await record_audit(
        db, actor=actor, action="tools.update", entity_type="tool", entity_id=str(t.id),
        detail={"fields": sorted(data)}, request=request,
    )
    await db.commit()
    names = await _names(db, {t.holder_employee_id})
    return _out(t, names, date.today())


@router.post("/{tool_id}/handover", response_model=ToolOut)
async def handover_tool(
    tool_id: str,
    body: HandoverBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(MANAGE),
):
    """Átadás másik dolgozónak (vagy visszavétel a raktárba: employee_id = null)."""
    t = await _tool_or_404(db, tool_id)
    to_id = await _employee_or_422(db, body.employee_id)
    db.add(ToolEvent(
        tool_id=t.id, kind="handover", from_employee_id=t.holder_employee_id, to_employee_id=to_id,
        note=(body.note or "").strip() or None, actor_name=actor.display_name,
    ))
    t.holder_employee_id = to_id
    await record_audit(
        db, actor=actor, action="tools.handover", entity_type="tool", entity_id=str(t.id),
        detail={"to": str(to_id) if to_id else None}, request=request,
    )
    await db.commit()
    names = await _names(db, {to_id})
    return _out(t, names, date.today())


@router.post("/{tool_id}/check", response_model=ToolOut)
async def check_tool(
    tool_id: str,
    body: CheckBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(MANAGE),
):
    """Ellenőrzés / kalibrálás rögzítése: last_check = ma, next_check a megadott
    dátum vagy az intervallumból számolva (nincs intervallum → marad)."""
    t = await _tool_or_404(db, tool_id)
    today = date.today()
    t.last_check = today
    if body.next_check is not None:
        t.next_check = body.next_check
    elif t.check_interval_months:
        m = today.month - 1 + t.check_interval_months
        y, m = today.year + m // 12, m % 12 + 1
        import calendar

        t.next_check = date(y, m, min(today.day, calendar.monthrange(y, m)[1]))
    db.add(ToolEvent(tool_id=t.id, kind="check", note=(body.note or "").strip() or None, actor_name=actor.display_name))
    await record_audit(
        db, actor=actor, action="tools.check", entity_type="tool", entity_id=str(t.id),
        detail={"next_check": t.next_check.isoformat() if t.next_check else None}, request=request,
    )
    await db.commit()
    names = await _names(db, {t.holder_employee_id})
    return _out(t, names, today)


@router.get("/{tool_id}/events", response_model=list[ToolEventOut])
async def tool_events(
    tool_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(MANAGE),
):
    t = await _tool_or_404(db, tool_id)
    rows = (
        await db.execute(select(ToolEvent).where(ToolEvent.tool_id == t.id).order_by(ToolEvent.created_at.desc()).limit(200))
    ).scalars().all()
    names = await _names(db, {e.from_employee_id for e in rows} | {e.to_employee_id for e in rows})
    return [
        ToolEventOut(
            id=str(e.id), kind=e.kind, from_name=names.get(e.from_employee_id), to_name=names.get(e.to_employee_id),
            note=e.note, actor_name=e.actor_name, created_at=e.created_at,
        )
        for e in rows
    ]


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(
    tool_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("delete")),
):
    t = await _tool_or_404(db, tool_id)
    await record_audit(
        db, actor=actor, action="tools.delete", entity_type="tool", entity_id=str(t.id),
        detail={"name": t.name}, request=request,
    )
    await db.delete(t)
    await db.commit()
    return None
