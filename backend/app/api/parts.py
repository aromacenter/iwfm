"""Alkatrész-raktár (parts modul, 1d553d2e — A fázis): cikktörzs, helykódos
raktárak (oszlop × sor), készlet, vételezés / áthelyezés / korrekció,
mozgásnapló. B fázis: szerelői autós raktár, beépítés munkalapra, jóváhagyásos
kiadás-kérés, min. készlet riasztás. A termék-raktártól (warehouse.py)
teljesen független; a Flotta-pulton a `parts` modullal kapcsolható."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_permission_matrix, record_audit, require_perm
from app.db import get_db
from app.models import Employee, Part, PartsMovement, PartsRequest, PartsStock, PartsWarehouse, Task, User, Worksheet
from app.services.wfm.license import require_module
from app.services.wfm.parts_service import UsedPartOut, used_parts_for_worksheet
from app.services.wfm.telegram import notify_event

router = APIRouter(dependencies=[Depends(require_module("parts"))])

MOVEMENT_KINDS = ("receipt", "issue", "use", "transfer", "adjust", "return", "scrap")
ADJUST_REASONS = ("count_error", "damage", "theft", "expired", "other")
_COL_RE = re.compile(r"^[A-Z0-9]{1,4}$")


def _uuid(raw: str, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail={"code": code})


# ─── Raktárak ────────────────────────────────────────────────────────────────


class WarehouseBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(default="central", pattern="^(central|vehicle|quarantine)$")
    owner_user_id: str | None = None
    managers: list[str] = Field(default_factory=list, max_length=50)  # raktáronkénti jog (üres = mindenki parts-joggal)
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
    managers: list[str] = []
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


def _can_manage(actor: User, wh: PartsWarehouse) -> None:
    """Raktáronkénti jog: ha a raktárnak van kezelő-listája, csak ők (és az
    admin) mozgathatnak benne — 403 parts.warehouse_forbidden."""
    if actor.role == "admin" or not wh.managers or str(actor.id) in [str(m) for m in wh.managers]:
        return
    raise HTTPException(status_code=403, detail={"code": "parts.warehouse_forbidden"})


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
        managers=[str(m) for m in (wh.managers or [])],
        part_count=int(cnt or 0), total_qty=float(total or 0.0),
    )


@router.get("/warehouses", response_model=list[WarehouseOut])
async def list_warehouses(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("parts")),
):
    rows = (await db.execute(select(PartsWarehouse).order_by(PartsWarehouse.kind, PartsWarehouse.name))).scalars().all()
    return [await _wh_out(db, w) for w in rows]


@router.post("/warehouses", response_model=WarehouseOut, status_code=201)
async def create_warehouse(
    body: WarehouseBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("parts")),
):
    wh = PartsWarehouse(
        name=body.name.strip(), kind=body.kind,
        owner_user_id=uuid.UUID(body.owner_user_id) if body.owner_user_id else None,
        columns=_normalize_columns(body.columns), rows=body.rows, notes=body.notes, is_active=body.is_active,
        managers=[str(uuid.UUID(m)) for m in body.managers],
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
    actor: User = Depends(require_perm("parts")),
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
    wh.managers = [str(uuid.UUID(m)) for m in body.managers]
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
    warranty_months: int | None = Field(default=None, ge=0, le=120)
    supplier_id: str | None = None
    reorder_qty: float | None = Field(default=None, ge=0)
    pack_unit: str | None = Field(default=None, max_length=16)
    pack_qty: float | None = Field(default=None, gt=0)
    compatible_models: list[str] = Field(default_factory=list, max_length=50)
    substitutes: list[str] = Field(default_factory=list, max_length=20)  # part id-k


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
    warranty_months: int | None = None
    supplier_id: str | None = None
    reorder_qty: float | None = None
    avg_cost: float | None = None
    pack_unit: str | None = None
    pack_qty: float | None = None
    compatible_models: list[str] = []
    substitutes: list[str] = []
    abc_class: str | None = None


def _part_out(p: Part, total: float = 0.0) -> PartOut:
    return PartOut(
        id=str(p.id), sku=p.sku, name=p.name, manufacturer_code=p.manufacturer_code, unit=p.unit,
        min_stock=p.min_stock, price_net=p.price_net, cost_net=p.cost_net, supplier=p.supplier,
        notes=p.notes, is_active=p.is_active, total_qty=total, low=p.min_stock > 0 and total < p.min_stock,
        warranty_months=p.warranty_months, supplier_id=str(p.supplier_id) if p.supplier_id else None,
        reorder_qty=p.reorder_qty, avg_cost=p.avg_cost, pack_unit=p.pack_unit, pack_qty=p.pack_qty,
        compatible_models=list(p.compatible_models or []), substitutes=[str(x) for x in (p.substitutes or [])],
        abc_class=p.abc_class,
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
    _: User = Depends(require_perm("parts")),
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
    actor: User = Depends(require_perm("parts")),
):
    sku = body.sku.strip().upper()
    dup = (await db.execute(select(Part).where(Part.sku == sku))).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=409, detail={"code": "parts.sku_taken"})
    p = Part(
        sku=sku, name=body.name.strip(), manufacturer_code=body.manufacturer_code, unit=body.unit.strip() or "db",
        min_stock=body.min_stock, price_net=body.price_net, cost_net=body.cost_net, supplier=body.supplier,
        notes=body.notes, is_active=body.is_active,
        warranty_months=body.warranty_months, supplier_id=uuid.UUID(body.supplier_id) if body.supplier_id else None,
        reorder_qty=body.reorder_qty, pack_unit=(body.pack_unit or None), pack_qty=body.pack_qty,
        compatible_models=[m.strip() for m in body.compatible_models if m.strip()],
        substitutes=[str(uuid.UUID(x)) for x in body.substitutes],
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
    actor: User = Depends(require_perm("parts")),
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
    p.warranty_months, p.reorder_qty = body.warranty_months, body.reorder_qty
    p.supplier_id = uuid.UUID(body.supplier_id) if body.supplier_id else None
    p.pack_unit, p.pack_qty = (body.pack_unit or None), body.pack_qty
    p.compatible_models = [m.strip() for m in body.compatible_models if m.strip()]
    p.substitutes = [str(uuid.UUID(x)) for x in body.substitutes if x != str(p.id)]
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
    if wh.kind == "vehicle" or not loc or not (wh.columns or []):
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
    document_no: str | None = None, note: str | None = None, price_net: float | None = None,
    serial_no: str | None = None, warranty_until: date | None = None, asset_id: uuid.UUID | None = None,
    reason: str | None = None,
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
        document_no=document_no, note=note, actor_user_id=actor.id, price_net=price_net,
        serial_no=(serial_no or None), warranty_until=warranty_until, asset_id=asset_id, reason=reason,
    ))
    return row


@router.get("/stock", response_model=list[StockOut])
async def list_stock(
    warehouse_id: str | None = None,
    part_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("parts")),
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
    in_packs: bool = False  # a quantity csomagban értendő (part.pack_qty-vel szorozva)


@router.post("/receipt", response_model=StockOut, status_code=201)
async def receipt(
    body: ReceiptBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("parts")),
):
    """Vételezés szállítótól egy raktár egy helykódjára. Mozgó átlagár:
    avg = (régi_össz × régi_avg + új × egységár) / (régi_össz + új)."""
    part = await _part_or_404(db, body.part_id)
    wh = await _wh_or_404(db, body.warehouse_id)
    _can_manage(actor, wh)
    loc = _validate_location(wh, body.location)
    qty = body.quantity * (part.pack_qty if body.in_packs and part.pack_qty else 1.0)
    unit_cost = body.unit_cost / (part.pack_qty if body.in_packs and part.pack_qty and body.unit_cost is not None else 1.0) if body.unit_cost is not None else None
    before = await _part_total(db, part.id)
    row = await _move(
        db, part=part, wh=wh, location=loc, kind="receipt", delta=qty, actor=actor,
        unit_cost=unit_cost, document_no=body.document_no, note=body.note,
    )
    if unit_cost is not None:
        _update_avg_cost(part, before, qty, unit_cost)  # ELŐBB az átlag (a régi cost_net a prior)
        part.cost_net = unit_cost  # utolsó beszerzési ár
    await record_audit(
        db, actor=actor, action="parts.receipt", entity_type="part", entity_id=str(part.id),
        detail={"warehouse": wh.name, "location": loc, "qty": qty}, request=request,
    )
    await db.commit()
    return StockOut(
        warehouse_id=str(wh.id), warehouse_name=wh.name, part_id=str(part.id), sku=part.sku, name=part.name,
        unit=part.unit, location=loc, quantity=row.quantity,
    )


def _update_avg_cost(part: Part, before_total: float, qty: float, unit_cost: float) -> None:
    """Mozgó átlagár frissítése vételezéskor."""
    base = max(0.0, before_total)
    prior = part.avg_cost if part.avg_cost is not None else part.cost_net  # első átlag: az addigi beszerzési ár
    if base <= 0 or prior is None:
        part.avg_cost = round(unit_cost, 2)
    else:
        part.avg_cost = round((base * prior + qty * unit_cost) / (base + qty), 2)


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
    actor: User = Depends(require_perm("parts")),
):
    """Áthelyezés raktárak / helykódok között (pl. központi → szerelő autója).
    Két mozgás-sor: transfer − a forráson, transfer + a célon."""
    part = await _part_or_404(db, body.part_id)
    src = await _wh_or_404(db, body.from_warehouse_id)
    dst = await _wh_or_404(db, body.to_warehouse_id)
    _can_manage(actor, src)
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
    reason: str | None = Field(default=None, pattern="^(count_error|damage|theft|expired|other)$")


@router.post("/adjust", response_model=StockOut)
async def adjust(
    body: AdjustBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("parts")),
):
    """Leltári korrekció: a megadott tényleges mennyiségre állít (a különbség
    kerül a naplóba)."""
    part = await _part_or_404(db, body.part_id)
    wh = await _wh_or_404(db, body.warehouse_id)
    _can_manage(actor, wh)
    loc = _validate_location(wh, body.location)
    row = await _stock_row(db, wh.id, part.id, loc)
    delta = round(body.quantity - row.quantity, 4)
    if abs(delta) > 1e-9:
        row = await _move(db, part=part, wh=wh, location=loc, kind="adjust", delta=delta, actor=actor, note=body.note, reason=body.reason)
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
    serial_no: str | None = None
    warranty_until: date | None = None
    reason: str | None = None


@router.get("/movements", response_model=list[MovementOut])
async def list_movements(
    warehouse_id: str | None = None,
    part_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("parts")),
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
            serial_no=m.serial_no, warranty_until=m.warranty_until, reason=m.reason,
        )
        for m, sku, pn, unit, whn in rows
    ]


# ─── B fázis: szerelői autós raktár, beépítés, kiadás-kérés ──────────────────


async def _has_perm(db: AsyncSession, user: User, feature: str) -> bool:
    if user.role == "admin":
        return True
    return feature in (await get_permission_matrix(db)).get(user.role, [])


async def _own_vehicle(db: AsyncSession, user_id: uuid.UUID) -> PartsWarehouse | None:
    """A bejelentkezett szerelő saját (aktív) autós raktára."""
    return (
        await db.execute(
            select(PartsWarehouse)
            .where(
                PartsWarehouse.kind == "vehicle", PartsWarehouse.owner_user_id == user_id,
                PartsWarehouse.is_active.is_(True),
            )
            .order_by(PartsWarehouse.created_at)
        )
    ).scalars().first()


async def _part_total(db: AsyncSession, part_id: uuid.UUID) -> float:
    total = (
        await db.execute(
            select(sa_func.coalesce(sa_func.sum(PartsStock.quantity), 0.0)).where(PartsStock.part_id == part_id)
        )
    ).scalar_one()
    return float(total or 0.0)


async def _low_check(db: AsyncSession, part: Part, before_total: float) -> None:
    """Min. készlet riasztás (Telegram, `parts.low`) — csak a küszöb ÁTLÉPÉSEKOR,
    hogy ne spameljen minden beépítésnél. Best-effort."""
    if part.min_stock <= 0:
        return
    after = await _part_total(db, part.id)
    if after < part.min_stock <= before_total:
        try:
            await notify_event(db, "parts.low", {
                "nev": part.name, "cikkszam": part.sku, "keszlet": f"{after:g}",
                "egyseg": part.unit, "minimum": f"{part.min_stock:g}",
            })
        except Exception:  # pragma: no cover
            import logging

            logging.getLogger(__name__).warning("parts.low notify failed", exc_info=True)


class CatalogItem(BaseModel):
    id: str
    sku: str
    name: str
    unit: str


class MyStockRow(BaseModel):
    part_id: str
    sku: str
    name: str
    unit: str
    quantity: float
    min_stock: float
    total_qty: float  # céges összes (minden raktár)
    low: bool


class PartsRequestOut(BaseModel):
    id: str
    part_id: str
    sku: str
    name: str
    unit: str
    quantity: float
    note: str | None
    status: str
    requester_user_id: str
    requester_name: str | None
    warehouse_id: str
    warehouse_name: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None
    issued_from_name: str | None
    issued_from_location: str | None
    issued_quantity: float | None
    received_at: datetime | None
    created_at: datetime


class MyPartsWarehouseOut(BaseModel):
    warehouse_id: str | None
    warehouse_name: str | None
    stock: list[MyStockRow] = []
    requests: list[PartsRequestOut] = []
    catalog: list[CatalogItem] = []  # kérhető cikkek (ár NÉLKÜL)


async def _requests_out(db: AsyncSession, rows: list[PartsRequest]) -> list[PartsRequestOut]:
    if not rows:
        return []
    parts = {
        p.id: p
        for p in (await db.execute(select(Part).where(Part.id.in_({r.part_id for r in rows})))).scalars().all()
    }
    wh_ids = {r.warehouse_id for r in rows} | {r.issued_from_warehouse_id for r in rows if r.issued_from_warehouse_id}
    whs = {
        w.id: w
        for w in (await db.execute(select(PartsWarehouse).where(PartsWarehouse.id.in_(wh_ids)))).scalars().all()
    }
    uids = {r.requester_user_id for r in rows} | {r.decided_by_user_id for r in rows if r.decided_by_user_id}
    users = {
        u.id: (u.display_name or u.email or "")
        for u in (await db.execute(select(User).where(User.id.in_(uids)))).scalars().all()
    }
    out = []
    for r in rows:
        p = parts.get(r.part_id)
        out.append(PartsRequestOut(
            id=str(r.id), part_id=str(r.part_id), sku=p.sku if p else "", name=p.name if p else "",
            unit=p.unit if p else "db", quantity=r.quantity, note=r.note, status=r.status,
            requester_user_id=str(r.requester_user_id), requester_name=users.get(r.requester_user_id),
            warehouse_id=str(r.warehouse_id),
            warehouse_name=whs[r.warehouse_id].name if r.warehouse_id in whs else None,
            decided_by_name=users.get(r.decided_by_user_id) if r.decided_by_user_id else None,
            decided_at=r.decided_at, decision_note=r.decision_note,
            issued_from_name=whs[r.issued_from_warehouse_id].name if r.issued_from_warehouse_id in whs else None,
            issued_from_location=r.issued_from_location, issued_quantity=r.issued_quantity,
            received_at=r.received_at, created_at=r.created_at,
        ))
    return out


@router.get("/my-warehouse", response_model=MyPartsWarehouseOut)
async def my_parts_warehouse(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A szerelő saját autós alkatrész-raktára: készlet + saját kérések +
    a kérhető cikkek listája (ár nélkül). Akinek nincs autós raktára, üreset kap."""
    wh = await _own_vehicle(db, actor.id)
    if wh is None:
        return MyPartsWarehouseOut(warehouse_id=None, warehouse_name=None)
    totals = await _totals(db)
    rows = (
        await db.execute(
            select(PartsStock, Part)
            .join(Part, Part.id == PartsStock.part_id)
            .where(PartsStock.warehouse_id == wh.id, PartsStock.quantity > 0)
            .order_by(Part.name)
        )
    ).all()
    reqs = (
        await db.execute(
            select(PartsRequest).where(PartsRequest.requester_user_id == actor.id)
            .order_by(PartsRequest.created_at.desc()).limit(50)
        )
    ).scalars().all()
    catalog = (await db.execute(select(Part).where(Part.is_active.is_(True)).order_by(Part.name))).scalars().all()
    return MyPartsWarehouseOut(
        warehouse_id=str(wh.id), warehouse_name=wh.name,
        stock=[
            MyStockRow(
                part_id=str(p.id), sku=p.sku, name=p.name, unit=p.unit, quantity=s.quantity, min_stock=p.min_stock,
                total_qty=totals.get(p.id, 0.0), low=p.min_stock > 0 and totals.get(p.id, 0.0) < p.min_stock,
            )
            for s, p in rows
        ],
        requests=await _requests_out(db, reqs),
        catalog=[CatalogItem(id=str(p.id), sku=p.sku, name=p.name, unit=p.unit) for p in catalog],
    )


# ── beépítés munkalapra ──


class UseBody(BaseModel):
    task_id: str
    part_id: str
    quantity: float = Field(gt=0)
    price_net: float | None = Field(default=None, ge=0)  # None = a cikktörzs ára
    # Csak settlements-joggal: tetszőleges raktár + helykód. Szerelőnek mindig
    # a saját autós raktára (szerveroldalon kikényszerítve).
    warehouse_id: str | None = None
    location: str = ""
    note: str | None = Field(default=None, max_length=512)
    serial_no: str | None = Field(default=None, max_length=64)
    warranty_until: date | None = None  # None = part.warranty_months a mai naptól


async def _task_and_worksheet(db: AsyncSession, task_id: str) -> tuple[Task, Worksheet]:
    task = (await db.execute(select(Task).where(Task.id == _uuid(task_id, "tasks.not_found")))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "tasks.not_found"})
    ws = (await db.execute(select(Worksheet).where(Worksheet.task_id == task.id))).scalar_one_or_none()
    if ws is None:
        raise HTTPException(status_code=409, detail={"code": "parts.worksheet_missing"})
    return task, ws


async def _assert_own_task(db: AsyncSession, actor: User, task: Task) -> None:
    emp = (await db.execute(select(Employee).where(Employee.user_id == actor.id))).scalars().first()
    if emp is None or task.employee_id != emp.id:  # idegen feladat: 404, mint a me-végpontokon
        raise HTTPException(status_code=404, detail={"code": "tasks.not_found"})


@router.post("/use", response_model=list[UsedPartOut], status_code=201)
async def use_part(
    body: UseBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """Alkatrész beépítése munkalapra: `use` mozgás (−) a szerelő SAJÁT autós
    raktárából — csak a saját feladatára. Settlements-joggal bármely raktárból,
    bármely munkalapra. Az ügyfél-ár pillanatképe a mozgásra kerül."""
    part = await _part_or_404(db, body.part_id)
    task, ws = await _task_and_worksheet(db, body.task_id)
    manager = await _has_perm(db, actor, "parts")
    if manager and body.warehouse_id:
        wh = await _wh_or_404(db, body.warehouse_id)
    else:
        wh = await _own_vehicle(db, actor.id)
        if wh is None:
            raise HTTPException(status_code=422, detail={"code": "parts.no_vehicle"})
        if not manager:
            await _assert_own_task(db, actor, task)
    loc = _validate_location(wh, body.location)
    before = await _part_total(db, part.id)
    price = body.price_net if body.price_net is not None else part.price_net
    warranty = body.warranty_until
    if warranty is None and part.warranty_months:
        warranty = date.today() + timedelta(days=int(part.warranty_months * 30.44))
    await _move(
        db, part=part, wh=wh, location=loc, kind="use", delta=-body.quantity, actor=actor,
        unit_cost=part.avg_cost if part.avg_cost is not None else part.cost_net, price_net=price,
        worksheet_id=ws.id, note=body.note, serial_no=body.serial_no, warranty_until=warranty, asset_id=ws.asset_id,
    )
    await record_audit(
        db, actor=actor, action="parts.use", entity_type="worksheet", entity_id=str(ws.id),
        detail={"sku": part.sku, "qty": body.quantity, "warehouse": wh.name, "serial": ws.serial}, request=request,
    )
    await db.commit()
    await _low_check(db, part, before)
    return await used_parts_for_worksheet(db, ws.id)


@router.delete("/use/{movement_id}", response_model=list[UsedPartOut])
async def unuse_part(
    movement_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """Beépítés visszavonása: storno `use` mozgás (+), az eredeti azonosítójával
    a document_no-ban — a készlet visszakerül, a napló nem törlődik."""
    mv = (
        await db.execute(
            select(PartsMovement).where(PartsMovement.id == _uuid(movement_id, "parts.movement_not_found"))
        )
    ).scalar_one_or_none()
    if mv is None or mv.kind != "use" or mv.quantity_delta >= 0 or mv.worksheet_id is None:
        raise HTTPException(status_code=404, detail={"code": "parts.movement_not_found"})
    if not await _has_perm(db, actor, "parts"):
        own = await _own_vehicle(db, actor.id)
        if own is None or own.id != mv.warehouse_id:
            raise HTTPException(status_code=404, detail={"code": "parts.movement_not_found"})
    already = (
        await db.execute(
            select(PartsMovement.id).where(PartsMovement.kind == "use", PartsMovement.document_no == str(mv.id))
        )
    ).first()
    if already is not None:
        raise HTTPException(status_code=409, detail={"code": "parts.already_reversed"})
    part = (await db.execute(select(Part).where(Part.id == mv.part_id))).scalar_one()
    wh = (await db.execute(select(PartsWarehouse).where(PartsWarehouse.id == mv.warehouse_id))).scalar_one()
    await _move(
        db, part=part, wh=wh, location=mv.location, kind="use", delta=-mv.quantity_delta, actor=actor,
        worksheet_id=mv.worksheet_id, document_no=str(mv.id), note="storno", asset_id=mv.asset_id,
    )
    await record_audit(
        db, actor=actor, action="parts.unuse", entity_type="worksheet", entity_id=str(mv.worksheet_id),
        detail={"sku": part.sku, "qty": -mv.quantity_delta}, request=request,
    )
    await db.commit()
    return await used_parts_for_worksheet(db, mv.worksheet_id)


# ── kiadás-kérések (jóváhagyásos) ──


class PartsRequestBody(BaseModel):
    part_id: str
    quantity: float = Field(gt=0)
    note: str | None = Field(default=None, max_length=512)


async def _req_or_404(db: AsyncSession, raw: str) -> PartsRequest:
    r = (
        await db.execute(select(PartsRequest).where(PartsRequest.id == _uuid(raw, "parts.request_not_found")))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail={"code": "parts.request_not_found"})
    return r


@router.post("/requests", response_model=PartsRequestOut, status_code=201)
async def create_parts_request(
    body: PartsRequestBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A szerelő alkatrészt kér a központból a SAJÁT autós raktárába —
    Telegram (`parts.request`) a raktárosnak, jóváhagyás az Alkatrészek oldalon."""
    wh = await _own_vehicle(db, actor.id)
    if wh is None:
        raise HTTPException(status_code=422, detail={"code": "parts.no_vehicle"})
    part = await _part_or_404(db, body.part_id)
    if not part.is_active:
        raise HTTPException(status_code=422, detail={"code": "parts.inactive"})
    req = PartsRequest(
        part_id=part.id, requester_user_id=actor.id, warehouse_id=wh.id, quantity=body.quantity,
        note=(body.note or "").strip() or None, status="pending",
    )
    db.add(req)
    await db.flush()
    await record_audit(
        db, actor=actor, action="parts.request_created", entity_type="parts_request", entity_id=str(req.id),
        detail={"sku": part.sku, "qty": body.quantity}, request=request,
    )
    await db.commit()
    try:
        await notify_event(db, "parts.request", {
            "nev": part.name, "cikkszam": part.sku, "mennyiseg": f"{body.quantity:g}", "egyseg": part.unit,
            "kero": actor.display_name or actor.email, "megjegyzes": f"— {req.note}" if req.note else "",
        })
    except Exception:  # pragma: no cover
        import logging

        logging.getLogger(__name__).warning("parts.request notify failed", exc_info=True)
    return (await _requests_out(db, [req]))[0]


@router.get("/requests", response_model=list[PartsRequestOut])
async def list_parts_requests(
    status: str | None = Query(default=None, max_length=12),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """Settlements-joggal az ÖSSZES kérés, egyébként csak a sajátok."""
    q = select(PartsRequest).order_by(PartsRequest.created_at.desc()).limit(200)
    if status:
        q = q.where(PartsRequest.status == status)
    if not await _has_perm(db, actor, "parts"):
        q = q.where(PartsRequest.requester_user_id == actor.id)
    return await _requests_out(db, (await db.execute(q)).scalars().all())


class IssueBody(BaseModel):
    from_warehouse_id: str
    from_location: str = ""
    quantity: float | None = Field(default=None, gt=0)  # None = a kért mennyiség
    note: str | None = Field(default=None, max_length=512)


@router.post("/requests/{request_id}/issue", response_model=PartsRequestOut)
async def issue_parts_request(
    request_id: str,
    body: IssueBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("parts")),
):
    """Jóváhagyás + kiadás helykódról: `issue` mozgás (− a forráson, + az
    autón) — a készlet AZONNAL a szerelő autójára kerül, ő csak nyugtázza."""
    req = await _req_or_404(db, request_id)
    if req.status != "pending":
        raise HTTPException(status_code=422, detail={"code": "parts.request_decided"})
    part = (await db.execute(select(Part).where(Part.id == req.part_id))).scalar_one()
    src = await _wh_or_404(db, body.from_warehouse_id)
    _can_manage(actor, src)
    dst = (await db.execute(select(PartsWarehouse).where(PartsWarehouse.id == req.warehouse_id))).scalar_one()
    if src.id == dst.id:
        raise HTTPException(status_code=422, detail={"code": "parts.same_location"})
    from_loc = _validate_location(src, body.from_location)
    qty = body.quantity if body.quantity is not None else req.quantity
    doc = f"REQ-{str(req.id)[:8]}"
    await _move(
        db, part=part, wh=src, location=from_loc, kind="issue", delta=-qty, actor=actor,
        ref_wh=dst.id, ref_location="", document_no=doc, note=body.note,
    )
    await _move(
        db, part=part, wh=dst, location="", kind="issue", delta=qty, actor=actor,
        ref_wh=src.id, ref_location=from_loc, document_no=doc, note=body.note,
    )
    req.status = "issued"
    req.decided_by_user_id, req.decided_at, req.decision_note = actor.id, datetime.now(UTC), body.note
    req.issued_from_warehouse_id, req.issued_from_location, req.issued_quantity = src.id, from_loc, qty
    await record_audit(
        db, actor=actor, action="parts.request_issued", entity_type="parts_request", entity_id=str(req.id),
        detail={"sku": part.sku, "qty": qty, "from": f"{src.name} {from_loc}".strip(), "to": dst.name},
        request=request,
    )
    await db.commit()
    return (await _requests_out(db, [req]))[0]


class DeclineBody(BaseModel):
    note: str | None = Field(default=None, max_length=512)


@router.post("/requests/{request_id}/decline", response_model=PartsRequestOut)
async def decline_parts_request(
    request_id: str,
    body: DeclineBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("parts")),
):
    req = await _req_or_404(db, request_id)
    if req.status != "pending":
        raise HTTPException(status_code=422, detail={"code": "parts.request_decided"})
    req.status, req.decided_by_user_id, req.decided_at = "declined", actor.id, datetime.now(UTC)
    req.decision_note = (body.note or "").strip() or None
    await record_audit(
        db, actor=actor, action="parts.request_declined", entity_type="parts_request", entity_id=str(req.id),
        detail={"note": req.decision_note}, request=request,
    )
    await db.commit()
    return (await _requests_out(db, [req]))[0]


@router.post("/requests/{request_id}/receive", response_model=PartsRequestOut)
async def receive_parts_request(
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A szerelő nyugtázza az átvételt (a készlet már a kiadáskor az autón van)."""
    req = await _req_or_404(db, request_id)
    if req.requester_user_id != actor.id:
        raise HTTPException(status_code=404, detail={"code": "parts.request_not_found"})
    if req.status != "issued":
        raise HTTPException(status_code=422, detail={"code": "parts.request_not_issued"})
    req.status, req.received_at = "received", datetime.now(UTC)
    await record_audit(
        db, actor=actor, action="parts.request_received", entity_type="parts_request", entity_id=str(req.id),
        detail={}, request=request,
    )
    await db.commit()
    return (await _requests_out(db, [req]))[0]


@router.post("/requests/{request_id}/cancel", response_model=PartsRequestOut)
async def cancel_parts_request(
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    req = await _req_or_404(db, request_id)
    if req.requester_user_id != actor.id:
        raise HTTPException(status_code=404, detail={"code": "parts.request_not_found"})
    if req.status != "pending":
        raise HTTPException(status_code=422, detail={"code": "parts.request_decided"})
    req.status, req.decided_at = "cancelled", datetime.now(UTC)
    await record_audit(
        db, actor=actor, action="parts.request_cancelled", entity_type="parts_request", entity_id=str(req.id),
        detail={}, request=request,
    )
    await db.commit()
    return (await _requests_out(db, [req]))[0]


# ─── D fázis: leltár + export ─────────────────────────────────────────────────


class SheetRow(BaseModel):
    part_id: str
    sku: str
    name: str
    unit: str
    location: str
    expected: float


class CountRow(BaseModel):
    part_id: str
    location: str = ""
    quantity: float = Field(ge=0)
    reason: str | None = Field(default=None, pattern="^(count_error|damage|theft|expired|other)$")


class StocktakeBody(BaseModel):
    warehouse_id: str
    note: str | None = Field(default=None, max_length=512)
    counts: list[CountRow] = Field(default_factory=list, max_length=2000)


class StocktakeDiff(BaseModel):
    part_id: str
    sku: str
    name: str
    location: str
    expected: float
    counted: float
    delta: float


class StocktakeOut(BaseModel):
    adjusted: int
    diffs: list[StocktakeDiff]


async def _sheet_rows(db: AsyncSession, wh: PartsWarehouse) -> list[SheetRow]:
    rows = (
        await db.execute(
            select(PartsStock, Part)
            .join(Part, Part.id == PartsStock.part_id)
            .where(PartsStock.warehouse_id == wh.id, PartsStock.quantity > 0)
            .order_by(PartsStock.location, Part.name)
        )
    ).all()
    return [
        SheetRow(part_id=str(p.id), sku=p.sku, name=p.name, unit=p.unit, location=s.location, expected=s.quantity)
        for s, p in rows
    ]


async def _apply_counts(
    db: AsyncSession, wh: PartsWarehouse, counts: list[CountRow], actor: User, note: str,
) -> StocktakeOut:
    """Minden számolt sor: ha eltér a nyilvántartástól → `adjust` mozgás a
    különbséggel (append-only). Nem számolt sor érintetlen marad."""
    diffs: list[StocktakeDiff] = []
    for c in counts:
        part = await _part_or_404(db, c.part_id)
        loc = _validate_location(wh, c.location)
        row = await _stock_row(db, wh.id, part.id, loc)
        delta = round(c.quantity - row.quantity, 4)
        if abs(delta) < 1e-9:
            continue
        expected = row.quantity
        await _move(db, part=part, wh=wh, location=loc, kind="adjust", delta=delta, actor=actor, note=note, reason=c.reason or "count_error")
        diffs.append(StocktakeDiff(
            part_id=str(part.id), sku=part.sku, name=part.name, location=loc,
            expected=expected, counted=c.quantity, delta=delta,
        ))
    return StocktakeOut(adjusted=len(diffs), diffs=diffs)


@router.get("/stocktake/sheet", response_model=list[SheetRow])
async def stocktake_sheet(
    warehouse_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("parts")),
):
    """Leltárív: a raktár nyilvántartott sorai helykódonként (ezt számolják)."""
    return await _sheet_rows(db, await _wh_or_404(db, warehouse_id))


@router.post("/stocktake", response_model=StocktakeOut)
async def stocktake(
    body: StocktakeBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("parts")),
):
    """Leltár könyvelése egy raktárra: a számolt mennyiségek → korrekciók."""
    wh = await _wh_or_404(db, body.warehouse_id)
    _can_manage(actor, wh)
    note = f"leltár: {body.note.strip()}" if body.note and body.note.strip() else "leltár"
    out = await _apply_counts(db, wh, body.counts, actor, note)
    await record_audit(
        db, actor=actor, action="parts.stocktake", entity_type="parts_warehouse", entity_id=str(wh.id),
        detail={"adjusted": out.adjusted, "counted": len(body.counts)}, request=request,
    )
    await db.commit()
    return out


class MyStocktakeBody(BaseModel):
    counts: list[CountRow] = Field(default_factory=list, max_length=500)


@router.post("/my-stocktake", response_model=StocktakeOut)
async def my_stocktake(
    body: MyStocktakeBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """„📋 Számoltam” — a szerelő a SAJÁT autóján leltároz; a különbség
    korrekcióként a mozgásnaplóba kerül (a vezető látja)."""
    wh = await _own_vehicle(db, actor.id)
    if wh is None:
        raise HTTPException(status_code=422, detail={"code": "parts.no_vehicle"})
    out = await _apply_counts(db, wh, body.counts, actor, "leltár (szerelő)")
    await record_audit(
        db, actor=actor, action="parts.stocktake", entity_type="parts_warehouse", entity_id=str(wh.id),
        detail={"adjusted": out.adjusted, "counted": len(body.counts), "own": True}, request=request,
    )
    await db.commit()
    return out


def _csv(rows: list[list], header: list[str]) -> Response:
    """Excel-barát CSV: UTF-8 BOM + pontosvessző; tizedes vesszővel."""
    import csv
    import io as _io

    buf = _io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([str(x).replace(".", ",") if isinstance(x, float) else ("" if x is None else x) for x in r])
    return Response(content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8")


@router.get("/export.csv")
async def export_csv(
    what: str = Query(default="stock", pattern="^(stock|movements|parts|valuation)$"),
    warehouse_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("parts")),
):
    """CSV export: készlet helykódonként | mozgásnapló | cikktörzs."""
    if what == "valuation":
        totals = await _totals(db)
        parts = (await db.execute(select(Part).where(Part.is_active.is_(True)).order_by(Part.sku))).scalars().all()
        resp = _csv(
            [[p.sku, p.name, float(totals.get(p.id, 0.0)), p.unit, p.avg_cost if p.avg_cost is not None else p.cost_net,
              round(float(totals.get(p.id, 0.0)) * float(p.avg_cost if p.avg_cost is not None else (p.cost_net or 0.0)), 2)] for p in parts],
            ["Cikkszám", "Megnevezés", "Készlet", "Egység", "Átlagár (nettó)", "Készletérték (nettó)"],
        )
        name = "alkatresz_keszletertek"
    elif what == "parts":
        totals = await _totals(db)
        parts = (await db.execute(select(Part).order_by(Part.sku))).scalars().all()
        resp = _csv(
            [[p.sku, p.name, p.manufacturer_code, p.unit, float(totals.get(p.id, 0.0)), float(p.min_stock),
              p.price_net, p.cost_net, p.supplier, "igen" if p.is_active else "nem"] for p in parts],
            ["Cikkszám", "Megnevezés", "Gyártói kód", "Egység", "Készlet összesen", "Min. készlet",
             "Eladási nettó", "Beszerzési nettó", "Beszállító", "Aktív"],
        )
        name = "alkatreszek"
    elif what == "movements":
        q = (
            select(PartsMovement, Part, PartsWarehouse.name)
            .join(Part, Part.id == PartsMovement.part_id)
            .join(PartsWarehouse, PartsWarehouse.id == PartsMovement.warehouse_id)
            .order_by(PartsMovement.created_at.desc())
            .limit(20000)
        )
        if warehouse_id:
            q = q.where(PartsMovement.warehouse_id == _uuid(warehouse_id, "parts.warehouse_not_found"))
        rows = (await db.execute(q)).all()
        wh_names = {w.id: w.name for w in (await db.execute(select(PartsWarehouse))).scalars().all()}
        actor_ids = {m.actor_user_id for m, _, _ in rows if m.actor_user_id}
        actors = {
            u.id: (u.display_name or u.email or "")
            for u in (await db.execute(select(User).where(User.id.in_(actor_ids)))).scalars().all()
        } if actor_ids else {}
        resp = _csv(
            [[m.created_at.strftime("%Y-%m-%d %H:%M"), m.kind, p.sku, p.name, whn, m.location,
              float(m.quantity_delta), p.unit, m.unit_cost, m.price_net,
              wh_names.get(m.ref_warehouse_id, "") if m.ref_warehouse_id else "", m.ref_location,
              m.document_no, m.note, actors.get(m.actor_user_id, "")] for m, p, whn in rows],
            ["Mikor", "Mozgás", "Cikkszám", "Megnevezés", "Raktár", "Helykód", "Változás", "Egység",
             "Egységár (beszerzés)", "Eladási ár", "Kapcsolódó raktár", "Kapcsolódó helykód", "Bizonylat",
             "Megjegyzés", "Ki"],
        )
        name = "alkatresz_mozgasok"
    else:
        q = (
            select(PartsStock, Part, PartsWarehouse.name)
            .join(Part, Part.id == PartsStock.part_id)
            .join(PartsWarehouse, PartsWarehouse.id == PartsStock.warehouse_id)
            .where(PartsStock.quantity > 0)
            .order_by(PartsWarehouse.name, PartsStock.location, Part.name)
        )
        if warehouse_id:
            q = q.where(PartsStock.warehouse_id == _uuid(warehouse_id, "parts.warehouse_not_found"))
        rows = (await db.execute(q)).all()
        resp = _csv(
            [[whn, s.location, p.sku, p.name, float(s.quantity), p.unit, p.price_net, p.cost_net] for s, p, whn in rows],
            ["Raktár", "Helykód", "Cikkszám", "Megnevezés", "Mennyiség", "Egység", "Eladási nettó", "Beszerzési nettó"],
        )
        name = "alkatresz_keszlet"
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}_{datetime.now(UTC):%Y%m%d}.csv"'
    return resp
