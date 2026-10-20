"""Alkatrész-raktár (parts modul, 1d553d2e — A fázis): cikktörzs, helykódos
raktárak (oszlop × sor), készlet, vételezés / áthelyezés / korrekció,
mozgásnapló. A termék-raktártól (warehouse.py) teljesen független; a
Flotta-pulton a `parts` modullal kapcsolható."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import Part, PartsMovement, PartsStock, PartsWarehouse, User
from app.services.wfm.license import require_module

router = APIRouter(dependencies=[Depends(require_module("parts"))])

MOVEMENT_KINDS = ("receipt", "issue", "use", "transfer", "adjust")
_COL_RE = re.compile(r"^[A-Z0-9]{1,4}$")


def _uuid(raw: str, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail={"code": code})


# ─── Raktárak ────────────────────────────────────────────────────────────────


class WarehouseBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(default="central", pattern="^(central|vehicle)$")
    owner_user_id: str | None = None
    columns: list[str] = Field(default_factory=lambda: ["A", "B", "C"], max_length=60)
    rows: int = Field(default=10, ge=1, le=999)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool = True


class WarehouseOut(BaseModel):
    id: str
    name: str
    kind: str
    owner_user_id: str | None
    columns: list[str]
    rows: int
    notes: str | None
    is_active: bool
    part_count: int = 0
    total_qty: float = 0.0


def _normalize_columns(cols: list[str]) -> list[str]:
    out: list[str] = []
    for c in cols:
        c = (c or "").strip().upper()
        if not c:
            continue
        if not _COL_RE.match(c):
            raise HTTPException(status_code=422, detail={"code": "parts.bad_column", "column": c})
        if c not in out:
            out.append(c)
    return out


async def _wh_or_404(db: AsyncSession, raw: str) -> PartsWarehouse:
    wh = (
        await db.execute(select(PartsWarehouse).where(PartsWarehouse.id == _uuid(raw, "parts.warehouse_not_found")))
    ).scalar_one_or_none()
    if wh is None:
        raise HTTPException(status_code=404, detail={"code": "parts.warehouse_not_found"})
    return wh


async def _wh_out(db: AsyncSession, wh: PartsWarehouse) -> WarehouseOut:
    cnt, total = (
        await db.execute(
            select(sa_func.count(sa_func.distinct(PartsStock.part_id)), sa_func.coalesce(sa_func.sum(PartsStock.quantity), 0.0))
            .where(PartsStock.warehouse_id == wh.id, PartsStock.quantity > 0)
        )
    ).one()
    return WarehouseOut(
        id=str(wh.id), name=wh.name, kind=wh.kind,
        owner_user_id=str(wh.owner_user_id) if wh.owner_user_id else None,
        columns=list(wh.columns or []), rows=wh.rows, notes=wh.notes, is_active=wh.is_active,
        part_count=int(cnt or 0), total_qty=float(total or 0.0),
    )


@router.get("/warehouses", response_model=list[WarehouseOut])
async def list_warehouses(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    rows = (await db.execute(select(PartsWarehouse).order_by(PartsWarehouse.kind, PartsWarehouse.name))).scalars().all()
    return [await _wh_out(db, w) for w in rows]


@router.post("/warehouses", response_model=WarehouseOut, status_code=201)
async def create_warehouse(
    body: WarehouseBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    wh = PartsWarehouse(
        name=body.name.strip(), kind=body.kind,
        owner_user_id=uuid.UUID(body.owner_user_id) if body.owner_user_id else None,
        columns=_normalize_columns(body.columns), rows=body.rows, notes=body.notes, is_active=body.is_active,
    )
    db.add(wh)
    await db.flush()
    await record_audit(
        db, actor=actor, action="parts.warehouse_created", entity_type="parts_warehouse",
        entity_id=str(wh.id), detail={"name": wh.name, "kind": wh.kind}, request=request,
    )
    await db.commit()
    return await _wh_out(db, wh)


@router.patch("/warehouses/{warehouse_id}", response_model=WarehouseOut)
async def update_warehouse(
    warehouse_id: str,
    body: WarehouseBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Oszlop/sor csak bővíthető úgy, hogy használt helykód ne vesszen el."""
    wh = await _wh_or_404(db, warehouse_id)
    cols = _normalize_columns(body.columns)
    used = (
        await db.execute(
            select(PartsStock.location).where(PartsStock.warehouse_id == wh.id, PartsStock.quantity > 0, PartsStock.location != "")
        )
    ).scalars().all()
    for loc in used:
        col, _, row = loc.partition("-")
        if col not in cols or (row.isdigit() and int(row) > body.rows):
            raise HTTPException(status_code=409, detail={"code": "parts.location_in_use", "location": loc})
    wh.name, wh.kind, wh.columns, wh.rows = body.name.strip(), body.kind, cols, body.rows
    wh.owner_user_id = uuid.UUID(body.owner_user_id) if body.owner_user_id else None
    wh.notes, wh.is_active = body.notes, body.is_active
    await record_audit(
        db, actor=actor, action="parts.warehouse_updated", entity_type="parts_warehouse",
        entity_id=str(wh.id), detail={"name": wh.name, "columns": cols, "rows": wh.rows}, request=request,
    )
    await db.commit()
    return await _wh_out(db, wh)


@router.delete("/warehouses/{warehouse_id}")
async def delete_warehouse(
    warehouse_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("delete")),
):
    wh = await _wh_or_404(db, warehouse_id)
    qty = (
        await db.execute(select(sa_func.coalesce(sa_func.sum(PartsStock.quantity), 0.0)).where(PartsStock.warehouse_id == wh.id))
    ).scalar_one()
    if float(qty or 0) > 0:
        raise HTTPException(status_code=409, detail={"code": "parts.warehouse_not_empty"})
    await db.delete(wh)
    await db.commit()
    return {"ok": True}


# ─── Cikktörzs ───────────────────────────────────────────────────────────────


class PartBody(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    manufacturer_code: str | None = Field(default=None, max_length=128)
    unit: str = Field(default="db", max_length=16)
    min_stock: float = Field(default=0.0, ge=0)
    price_net: float | None = Field(default=None, ge=0)
    cost_net: float | None = Field(default=None, ge=0)
    supplier: str | None = Field(default=None, max_length=256)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool = True


class PartOut(BaseModel):
    id: str
    sku: str
    name: str
    manufacturer_code: str | None
    unit: str
    min_stock: float
    price_net: float | None
    cost_net: float | None
    supplier: str | None
    notes: str | None
    is_active: bool
    total_qty: float = 0.0
    low: bool = False


def _part_out(p: Part, total: float = 0.0) -> PartOut:
    return PartOut(
        id=str(p.id), sku=p.sku, name=p.name, manufacturer_code=p.manufacturer_code, unit=p.unit,
        min_stock=p.min_stock, price_net=p.price_net, cost_net=p.cost_net, supplier=p.supplier,
        notes=p.notes, is_active=p.is_active, total_qty=total, low=p.min_stock > 0 and total < p.min_stock,
    )


async def _part_or_404(db: AsyncSession, raw: str) -> Part:
    p = (await db.execute(select(Part).where(Part.id == _uuid(raw, "parts.not_found")))).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "parts.not_found"})
    return p


async def _totals(db: AsyncSession) -> dict[uuid.UUID, float]:
    rows = (
        await db.execute(select(PartsStock.part_id, sa_func.sum(PartsStock.quantity)).group_by(PartsStock.part_id))
    ).all()
    return {pid: float(q or 0) for pid, q in rows}


@router.get("", response_model=list[PartOut])
async def list_parts(
    q: str | None = Query(default=None, max_length=128),
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    query = select(Part).order_by(Part.name)
    if not include_inactive:
        query = query.where(Part.is_active.is_(True))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(Part.name.ilike(like) | Part.sku.ilike(like) | Part.manufacturer_code.ilike(like))
    totals = await _totals(db)
    return [_part_out(p, totals.get(p.id, 0.0)) for p in (await db.execute(query)).scalars().all()]


@router.post("", response_model=PartOut, status_code=201)
async def create_part(
    body: PartBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    sku = body.sku.strip().upper()
    dup = (await db.execute(select(Part).where(Part.sku == sku))).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=409, detail={"code": "parts.sku_taken"})
    p = Part(
        sku=sku, name=body.name.strip(), manufacturer_code=body.manufacturer_code, unit=body.unit.strip() or "db",
        min_stock=body.min_stock, price_net=body.price_net, cost_net=body.cost_net, supplier=body.supplier,
        notes=body.notes, is_active=body.is_active,
    )
    db.add(p)
    await db.flush()
    await record_audit(
        db, actor=actor, action="parts.created", entity_type="part", entity_id=str(p.id),
        detail={"sku": p.sku, "name": p.name}, request=request,
    )
    await db.commit()
    return _part_out(p)


@router.patch("/{part_id}", response_model=PartOut)
async def update_part(
    part_id: str,
    body: PartBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    p = await _part_or_404(db, part_id)
    sku = body.sku.strip().upper()
    if sku != p.sku:
        dup = (await db.execute(select(Part).where(Part.sku == sku))).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(status_code=409, detail={"code": "parts.sku_taken"})
    p.sku, p.name, p.manufacturer_code, p.unit = sku, body.name.strip(), body.manufacturer_code, body.unit.strip() or "db"
    p.min_stock, p.price_net, p.cost_net, p.supplier = body.min_stock, body.price_net, body.cost_net, body.supplier
    p.notes, p.is_active = body.notes, body.is_active
    await record_audit(
        db, actor=actor, action="parts.updated", entity_type="part", entity_id=str(p.id),
        detail={"sku": p.sku, "name": p.name}, request=request,
    )
    await db.commit()
    return _part_out(p, (await _totals(db)).get(p.id, 0.0))


# ─── Készlet + mozgások ──────────────────────────────────────────────────────


class StockOut(BaseModel):
    warehouse_id: str
    warehouse_name: str
    part_id: str
    sku: str
    name: str
    unit: str
    location: str
    quantity: float


def _validate_location(wh: PartsWarehouse, location: str) -> str:
    """Helykód: "OSZLOP-SOR" (pl. B-12) a raktár oszlopai/sorai közül; autós
    raktárnál üres."""
    loc = (location or "").strip().upper()
    if wh.kind == "vehicle" or not loc:
        return ""
    col, sep, row = loc.partition("-")
    if not sep or col not in (wh.columns or []) or not row.isdigit() or not (1 <= int(row) <= wh.rows):
        raise HTTPException(status_code=422, detail={"code": "parts.bad_location", "location": loc})
    return f"{col}-{int(row)}"


async def _stock_row(db: AsyncSession, wh_id: uuid.UUID, part_id: uuid.UUID, location: str) -> PartsStock:
    row = (
        await db.execute(
            select(PartsStock).where(
                PartsStock.warehouse_id == wh_id, PartsStock.part_id == part_id, PartsStock.location == location
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = PartsStock(warehouse_id=wh_id, part_id=part_id, location=location, quantity=0.0)
        db.add(row)
        await db.flush()
    return row


async def _move(
    db: AsyncSession, *, part: Part, wh: PartsWarehouse, location: str, kind: str, delta: float,
    actor: User, unit_cost: float | None = None, ref_wh: uuid.UUID | None = None,
    ref_location: str | None = None, worksheet_id: uuid.UUID | None = None,
    document_no: str | None = None, note: str | None = None,
) -> PartsStock:
    """Egy mozgás: készlet-sor frissítése + append-only napló. Negatívba nem mehet."""
    row = await _stock_row(db, wh.id, part.id, location)
    if row.quantity + delta < -1e-9:
        raise HTTPException(
            status_code=409,
            detail={"code": "parts.insufficient_stock", "available": row.quantity, "requested": -delta},
        )
    row.quantity = round(row.quantity + delta, 4)
    db.add(PartsMovement(
        part_id=part.id, warehouse_id=wh.id, location=location, kind=kind, quantity_delta=delta,
        unit_cost=unit_cost, ref_warehouse_id=ref_wh, ref_location=ref_location, worksheet_id=worksheet_id,
        document_no=document_no, note=note, actor_user_id=actor.id,
    ))
    return row


@router.get("/stock", response_model=list[StockOut])
async def list_stock(
    warehouse_id: str | None = None,
    part_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    query = (
        select(PartsStock, PartsWarehouse.name, Part.sku, Part.name, Part.unit)
        .join(PartsWarehouse, PartsWarehouse.id == PartsStock.warehouse_id)
        .join(Part, Part.id == PartsStock.part_id)
        .where(PartsStock.quantity > 0)
        .order_by(PartsWarehouse.name, PartsStock.location, Part.name)
    )
    if warehouse_id:
        query = query.where(PartsStock.warehouse_id == _uuid(warehouse_id, "parts.warehouse_not_found"))
    if part_id:
        query = query.where(PartsStock.part_id == _uuid(part_id, "parts.not_found"))
    return [
        StockOut(
            warehouse_id=str(s.warehouse_id), warehouse_name=whn, part_id=str(s.part_id), sku=sku, name=pn,
            unit=unit, location=s.location, quantity=s.quantity,
        )
        for s, whn, sku, pn, unit in (await db.execute(query)).all()
    ]


class ReceiptBody(BaseModel):
    part_id: str
    warehouse_id: str
    location: str = ""
    quantity: float = Field(gt=0)
    unit_cost: float | None = Field(default=None, ge=0)
    document_no: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=512)


@router.post("/receipt", response_model=StockOut, status_code=201)
async def receipt(
    body: ReceiptBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Vételezés szállítótól egy raktár egy helykódjára."""
    part = await _part_or_404(db, body.part_id)
    wh = await _wh_or_404(db, body.warehouse_id)
    loc = _validate_location(wh, body.location)
    row = await _move(
        db, part=part, wh=wh, location=loc, kind="receipt", delta=body.quantity, actor=actor,
        unit_cost=body.unit_cost, document_no=body.document_no, note=body.note,
    )
    if body.unit_cost is not None:
        part.cost_net = body.unit_cost  # utolsó beszerzési ár
    await record_audit(
        db, actor=actor, action="parts.receipt", entity_type="part", entity_id=str(part.id),
        detail={"warehouse": wh.name, "location": loc, "qty": body.quantity}, request=request,
    )
    await db.commit()
    return StockOut(
        warehouse_id=str(wh.id), warehouse_name=wh.name, part_id=str(part.id), sku=part.sku, name=part.name,
        unit=part.unit, location=loc, quantity=row.quantity,
    )


class TransferBody(BaseModel):
    part_id: str
    from_warehouse_id: str
    from_location: str = ""
    to_warehouse_id: str
    to_location: str = ""
    quantity: float = Field(gt=0)
    note: str | None = Field(default=None, max_length=512)


@router.post("/transfer", status_code=201)
async def transfer(
    body: TransferBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Áthelyezés raktárak / helykódok között (pl. központi → szerelő autója).
    Két mozgás-sor: transfer − a forráson, transfer + a célon."""
    part = await _part_or_404(db, body.part_id)
    src = await _wh_or_404(db, body.from_warehouse_id)
    dst = await _wh_or_404(db, body.to_warehouse_id)
    from_loc = _validate_location(src, body.from_location)
    to_loc = _validate_location(dst, body.to_location)
    if src.id == dst.id and from_loc == to_loc:
        raise HTTPException(status_code=422, detail={"code": "parts.same_location"})
    await _move(
        db, part=part, wh=src, location=from_loc, kind="transfer", delta=-body.quantity, actor=actor,
        ref_wh=dst.id, ref_location=to_loc, note=body.note,
    )
    row = await _move(
        db, part=part, wh=dst, location=to_loc, kind="transfer", delta=body.quantity, actor=actor,
        ref_wh=src.id, ref_location=from_loc, note=body.note,
    )
    await record_audit(
        db, actor=actor, action="parts.transfer", entity_type="part", entity_id=str(part.id),
        detail={"from": f"{src.name} {from_loc}".strip(), "to": f"{dst.name} {to_loc}".strip(), "qty": body.quantity},
        request=request,
    )
    await db.commit()
    return {"ok": True, "to_quantity": row.quantity}


class AdjustBody(BaseModel):
    part_id: str
    warehouse_id: str
    location: str = ""
    quantity: float = Field(ge=0)  # a tényleges (számolt) mennyiség
    note: str | None = Field(default=None, max_length=512)


@router.post("/adjust", response_model=StockOut)
async def adjust(
    body: AdjustBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Leltári korrekció: a megadott tényleges mennyiségre állít (a különbség
    kerül a naplóba)."""
    part = await _part_or_404(db, body.part_id)
    wh = await _wh_or_404(db, body.warehouse_id)
    loc = _validate_location(wh, body.location)
    row = await _stock_row(db, wh.id, part.id, loc)
    delta = round(body.quantity - row.quantity, 4)
    if abs(delta) > 1e-9:
        row = await _move(db, part=part, wh=wh, location=loc, kind="adjust", delta=delta, actor=actor, note=body.note)
    await record_audit(
        db, actor=actor, action="parts.adjust", entity_type="part", entity_id=str(part.id),
        detail={"warehouse": wh.name, "location": loc, "delta": delta}, request=request,
    )
    await db.commit()
    return StockOut(
        warehouse_id=str(wh.id), warehouse_name=wh.name, part_id=str(part.id), sku=part.sku, name=part.name,
        unit=part.unit, location=loc, quantity=row.quantity,
    )


class MovementOut(BaseModel):
    id: str
    created_at: datetime
    kind: str
    part_id: str
    sku: str
    name: str
    unit: str
    warehouse_id: str
    warehouse_name: str
    location: str
    quantity_delta: float
    unit_cost: float | None
    ref_warehouse_name: str | None
    ref_location: str | None
    document_no: str | None
    note: str | None
    actor_name: str | None


@router.get("/movements", response_model=list[MovementOut])
async def list_movements(
    warehouse_id: str | None = None,
    part_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    query = (
        select(PartsMovement, Part.sku, Part.name, Part.unit, PartsWarehouse.name)
        .join(Part, Part.id == PartsMovement.part_id)
        .join(PartsWarehouse, PartsWarehouse.id == PartsMovement.warehouse_id)
        .order_by(PartsMovement.created_at.desc())
        .limit(limit)
    )
    if warehouse_id:
        query = query.where(PartsMovement.warehouse_id == _uuid(warehouse_id, "parts.warehouse_not_found"))
    if part_id:
        query = query.where(PartsMovement.part_id == _uuid(part_id, "parts.not_found"))
    rows = (await db.execute(query)).all()
    wh_names = {w.id: w.name for w in (await db.execute(select(PartsWarehouse))).scalars().all()}
    actor_ids = {m.actor_user_id for m, *_ in rows if m.actor_user_id}
    actors: dict[uuid.UUID, str] = {}
    if actor_ids:
        for u in (await db.execute(select(User).where(User.id.in_(actor_ids)))).scalars().all():
            actors[u.id] = u.display_name or u.email or ""
    return [
        MovementOut(
            id=str(m.id), created_at=m.created_at, kind=m.kind, part_id=str(m.part_id), sku=sku, name=pn, unit=unit,
            warehouse_id=str(m.warehouse_id), warehouse_name=whn, location=m.location,
            quantity_delta=m.quantity_delta, unit_cost=m.unit_cost,
            ref_warehouse_name=wh_names.get(m.ref_warehouse_id) if m.ref_warehouse_id else None,
            ref_location=m.ref_location, document_no=m.document_no, note=m.note,
            actor_name=actors.get(m.actor_user_id) if m.actor_user_id else None,
        )
        for m, sku, pn, unit, whn in rows
    ]
