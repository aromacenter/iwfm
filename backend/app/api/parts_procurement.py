"""Alkatrész-raktár — beszerzés és pénz (ERP 2. kör, 1d553d2e): beszállítói
törzs + árlista, beszerzési rendelés (draft → ordered → partial → received),
vételezés rendelésre, újrarendelési javaslat, készletérték (mozgó átlagár),
karantén / visszáru / garanciaigény, kitek (szerviz-szett) + kit-beépítés.
Ugyanaz a /api/parts prefix és a `parts` modulkapcsoló, mint a parts.py."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, record_audit, require_perm
from app.api.parts import (
    _assert_own_task, _can_manage, _has_perm, _low_check, _move, _own_vehicle, _part_or_404, _part_total,
    _task_and_worksheet, _totals, _update_avg_cost, _uuid, _validate_location, _wh_or_404,
)
from app.db import get_db
from app.models import (
    Part, PartsKit, PartsKitItem, PartsPurchaseItem, PartsPurchaseOrder, PartsReturn, PartsStock, PartsSupplier,
    PartsSupplierPrice, PartsWarehouse, User,
)
from app.services.wfm.license import require_module
from app.services.wfm.parts_service import UsedPartOut, used_parts_for_worksheet

router = APIRouter(dependencies=[Depends(require_module("parts"))])


# ─── Beszállítók + árlista ───────────────────────────────────────────────────


class SupplierBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    contact: str | None = Field(default=None, max_length=256)
    lead_days: int = Field(default=3, ge=0, le=365)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool = True


class SupplierPriceOut(BaseModel):
    part_id: str
    sku: str
    name: str
    price_net: float
    supplier_sku: str | None
    moq: float
    lead_days: int | None


class SupplierOut(BaseModel):
    id: str
    name: str
    email: str | None
    phone: str | None
    contact: str | None
    lead_days: int
    notes: str | None
    is_active: bool
    prices: list[SupplierPriceOut] = []


async def _supplier_or_404(db: AsyncSession, raw: str) -> PartsSupplier:
    s = (await db.execute(select(PartsSupplier).where(PartsSupplier.id == _uuid(raw, "parts.supplier_not_found")))).scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail={"code": "parts.supplier_not_found"})
    return s


async def _supplier_out(db: AsyncSession, s: PartsSupplier) -> SupplierOut:
    rows = (
        await db.execute(
            select(PartsSupplierPrice, Part).join(Part, Part.id == PartsSupplierPrice.part_id)
            .where(PartsSupplierPrice.supplier_id == s.id).order_by(Part.name)
        )
    ).all()
    return SupplierOut(
        id=str(s.id), name=s.name, email=s.email, phone=s.phone, contact=s.contact, lead_days=s.lead_days,
        notes=s.notes, is_active=s.is_active,
        prices=[
            SupplierPriceOut(part_id=str(p.id), sku=p.sku, name=p.name, price_net=sp.price_net,
                             supplier_sku=sp.supplier_sku, moq=sp.moq, lead_days=sp.lead_days)
            for sp, p in rows
        ],
    )


@router.get("/suppliers", response_model=list[SupplierOut])
async def list_suppliers(db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts"))):
    rows = (await db.execute(select(PartsSupplier).order_by(PartsSupplier.name))).scalars().all()
    return [await _supplier_out(db, s) for s in rows]


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
async def create_supplier(
    body: SupplierBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts")),
):
    s = PartsSupplier(**body.model_dump())
    s.name = s.name.strip()
    db.add(s)
    await db.flush()
    await record_audit(db, actor=actor, action="parts.supplier_created", entity_type="parts_supplier", entity_id=str(s.id), detail={"name": s.name}, request=request)
    await db.commit()
    return await _supplier_out(db, s)


@router.patch("/suppliers/{supplier_id}", response_model=SupplierOut)
async def update_supplier(
    supplier_id: str, body: SupplierBody, request: Request,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts")),
):
    s = await _supplier_or_404(db, supplier_id)
    for k, v in body.model_dump().items():
        setattr(s, k, v.strip() if isinstance(v, str) and k == "name" else v)
    await record_audit(db, actor=actor, action="parts.supplier_updated", entity_type="parts_supplier", entity_id=str(s.id), detail={"name": s.name}, request=request)
    await db.commit()
    return await _supplier_out(db, s)


class SupplierPriceBody(BaseModel):
    part_id: str
    price_net: float = Field(ge=0)
    supplier_sku: str | None = Field(default=None, max_length=64)
    moq: float = Field(default=1.0, gt=0)
    lead_days: int | None = Field(default=None, ge=0, le=365)


@router.put("/suppliers/{supplier_id}/prices", response_model=SupplierOut)
async def upsert_supplier_prices(
    supplier_id: str, body: list[SupplierPriceBody], request: Request,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts")),
):
    """Árlista-sorok felvitele/frissítése (cikkenként egy sor)."""
    s = await _supplier_or_404(db, supplier_id)
    for row in body[:500]:
        part = await _part_or_404(db, row.part_id)
        sp = (
            await db.execute(select(PartsSupplierPrice).where(PartsSupplierPrice.supplier_id == s.id, PartsSupplierPrice.part_id == part.id))
        ).scalar_one_or_none()
        if sp is None:
            sp = PartsSupplierPrice(supplier_id=s.id, part_id=part.id, price_net=row.price_net)
            db.add(sp)
        sp.price_net, sp.supplier_sku, sp.moq, sp.lead_days = row.price_net, row.supplier_sku, row.moq, row.lead_days
        sp.updated_at = datetime.now(UTC)
    await record_audit(db, actor=actor, action="parts.supplier_prices", entity_type="parts_supplier", entity_id=str(s.id), detail={"rows": len(body)}, request=request)
    await db.commit()
    return await _supplier_out(db, s)


@router.delete("/suppliers/{supplier_id}/prices/{part_id}", response_model=SupplierOut)
async def delete_supplier_price(
    supplier_id: str, part_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts")),
):
    s = await _supplier_or_404(db, supplier_id)
    sp = (
        await db.execute(select(PartsSupplierPrice).where(PartsSupplierPrice.supplier_id == s.id, PartsSupplierPrice.part_id == _uuid(part_id, "parts.not_found")))
    ).scalar_one_or_none()
    if sp is not None:
        await db.delete(sp)
        await db.commit()
    return await _supplier_out(db, s)


# ─── Beszerzési rendelések ───────────────────────────────────────────────────


class POItemBody(BaseModel):
    part_id: str
    qty_ordered: float = Field(gt=0)
    unit_cost: float | None = Field(default=None, ge=0)


class POBody(BaseModel):
    supplier_id: str | None = None
    expected_date: date | None = None
    note: str | None = Field(default=None, max_length=1000)
    items: list[POItemBody] = Field(default_factory=list, max_length=200)


class POItemOut(BaseModel):
    id: str
    part_id: str
    sku: str
    name: str
    unit: str
    qty_ordered: float
    qty_received: float
    unit_cost: float | None


class POOut(BaseModel):
    id: str
    number: str
    supplier_id: str | None
    supplier_name: str | None
    status: str
    expected_date: date | None
    note: str | None
    created_at: datetime
    ordered_at: datetime | None
    received_at: datetime | None
    total_net: float
    items: list[POItemOut]


async def _po_or_404(db: AsyncSession, raw: str) -> PartsPurchaseOrder:
    po = (await db.execute(select(PartsPurchaseOrder).where(PartsPurchaseOrder.id == _uuid(raw, "parts.po_not_found")))).scalar_one_or_none()
    if po is None:
        raise HTTPException(status_code=404, detail={"code": "parts.po_not_found"})
    return po


async def _po_out(db: AsyncSession, po: PartsPurchaseOrder) -> POOut:
    rows = (
        await db.execute(
            select(PartsPurchaseItem, Part).join(Part, Part.id == PartsPurchaseItem.part_id)
            .where(PartsPurchaseItem.po_id == po.id).order_by(Part.name)
        )
    ).all()
    sup = (await db.execute(select(PartsSupplier).where(PartsSupplier.id == po.supplier_id))).scalar_one_or_none() if po.supplier_id else None
    items = [
        POItemOut(id=str(it.id), part_id=str(p.id), sku=p.sku, name=p.name, unit=p.unit, qty_ordered=it.qty_ordered,
                  qty_received=it.qty_received, unit_cost=it.unit_cost)
        for it, p in rows
    ]
    return POOut(
        id=str(po.id), number=po.number, supplier_id=str(po.supplier_id) if po.supplier_id else None,
        supplier_name=sup.name if sup else None, status=po.status, expected_date=po.expected_date, note=po.note,
        created_at=po.created_at, ordered_at=po.ordered_at, received_at=po.received_at,
        total_net=round(sum(i.qty_ordered * (i.unit_cost or 0.0) for i in items), 2), items=items,
    )


async def _next_po_number(db: AsyncSession) -> str:
    year = datetime.now(UTC).year
    prefix = f"PO-{year}-"
    cnt = (await db.execute(select(sa_func.count()).select_from(PartsPurchaseOrder).where(PartsPurchaseOrder.number.like(prefix + "%")))).scalar_one()
    return f"{prefix}{int(cnt or 0) + 1:04d}"


async def _default_cost(db: AsyncSession, part: Part, supplier_id: uuid.UUID | None) -> float | None:
    """Egységár a rendelésre: beszállítói árlista → utolsó beszerzési ár."""
    if supplier_id is not None:
        sp = (
            await db.execute(select(PartsSupplierPrice).where(PartsSupplierPrice.supplier_id == supplier_id, PartsSupplierPrice.part_id == part.id))
        ).scalar_one_or_none()
        if sp is not None:
            return sp.price_net
    return part.cost_net


async def _set_po_items(db: AsyncSession, po: PartsPurchaseOrder, items: list[POItemBody]) -> None:
    for old in (await db.execute(select(PartsPurchaseItem).where(PartsPurchaseItem.po_id == po.id))).scalars().all():
        await db.delete(old)
    await db.flush()
    for it in items:
        part = await _part_or_404(db, it.part_id)
        db.add(PartsPurchaseItem(
            po_id=po.id, part_id=part.id, qty_ordered=it.qty_ordered,
            unit_cost=it.unit_cost if it.unit_cost is not None else await _default_cost(db, part, po.supplier_id),
        ))


@router.get("/purchase-orders", response_model=list[POOut])
async def list_pos(
    status: str | None = Query(default=None, max_length=12),
    db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts")),
):
    q = select(PartsPurchaseOrder).order_by(PartsPurchaseOrder.created_at.desc()).limit(300)
    if status:
        q = q.where(PartsPurchaseOrder.status == status)
    return [await _po_out(db, po) for po in (await db.execute(q)).scalars().all()]


@router.post("/purchase-orders", response_model=POOut, status_code=201)
async def create_po(body: POBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    if not body.items:
        raise HTTPException(status_code=422, detail={"code": "parts.po_empty"})
    sup = await _supplier_or_404(db, body.supplier_id) if body.supplier_id else None
    po = PartsPurchaseOrder(number=await _next_po_number(db), supplier_id=sup.id if sup else None, status="draft",
                            expected_date=body.expected_date, note=body.note, created_by=actor.id)
    db.add(po)
    await db.flush()
    await _set_po_items(db, po, body.items)
    await record_audit(db, actor=actor, action="parts.po_created", entity_type="parts_po", entity_id=str(po.id), detail={"number": po.number, "items": len(body.items)}, request=request)
    await db.commit()
    return await _po_out(db, po)


@router.patch("/purchase-orders/{po_id}", response_model=POOut)
async def update_po(po_id: str, body: POBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    """Csak piszkozat szerkeszthető."""
    po = await _po_or_404(db, po_id)
    if po.status != "draft":
        raise HTTPException(status_code=422, detail={"code": "parts.po_not_draft"})
    if not body.items:
        raise HTTPException(status_code=422, detail={"code": "parts.po_empty"})
    po.supplier_id = (await _supplier_or_404(db, body.supplier_id)).id if body.supplier_id else None
    po.expected_date, po.note = body.expected_date, body.note
    await _set_po_items(db, po, body.items)
    await record_audit(db, actor=actor, action="parts.po_updated", entity_type="parts_po", entity_id=str(po.id), detail={"number": po.number}, request=request)
    await db.commit()
    return await _po_out(db, po)


@router.post("/purchase-orders/{po_id}/order", response_model=POOut)
async def order_po(po_id: str, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    po = await _po_or_404(db, po_id)
    if po.status != "draft":
        raise HTTPException(status_code=422, detail={"code": "parts.po_not_draft"})
    po.status, po.ordered_at = "ordered", datetime.now(UTC)
    await record_audit(db, actor=actor, action="parts.po_ordered", entity_type="parts_po", entity_id=str(po.id), detail={"number": po.number}, request=request)
    await db.commit()
    return await _po_out(db, po)


@router.post("/purchase-orders/{po_id}/cancel", response_model=POOut)
async def cancel_po(po_id: str, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    po = await _po_or_404(db, po_id)
    if po.status in ("received", "cancelled"):
        raise HTTPException(status_code=422, detail={"code": "parts.po_closed"})
    po.status = "cancelled"
    await record_audit(db, actor=actor, action="parts.po_cancelled", entity_type="parts_po", entity_id=str(po.id), detail={"number": po.number}, request=request)
    await db.commit()
    return await _po_out(db, po)


class ReceiveLine(BaseModel):
    item_id: str
    quantity: float = Field(gt=0)
    location: str = ""
    unit_cost: float | None = Field(default=None, ge=0)


class ReceiveBody(BaseModel):
    warehouse_id: str
    lines: list[ReceiveLine] = Field(min_length=1, max_length=200)
    document_no: str | None = Field(default=None, max_length=64)


@router.post("/purchase-orders/{po_id}/receive", response_model=POOut)
async def receive_po(po_id: str, body: ReceiveBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    """Vételezés rendelésre: soronként `receipt` mozgás (bizonylat = a PO
    száma / szállítólevél), qty_received nő, státusz partial → received, ha
    minden sor beérkezett. Túlszállítás engedett (a hátralék 0-ra áll)."""
    po = await _po_or_404(db, po_id)
    if po.status not in ("ordered", "partial"):
        raise HTTPException(status_code=422, detail={"code": "parts.po_not_ordered"})
    wh = await _wh_or_404(db, body.warehouse_id)
    _can_manage(actor, wh)
    items = {str(it.id): it for it in (await db.execute(select(PartsPurchaseItem).where(PartsPurchaseItem.po_id == po.id))).scalars().all()}
    for line in body.lines:
        it = items.get(line.item_id)
        if it is None:
            raise HTTPException(status_code=404, detail={"code": "parts.po_item_not_found"})
        part = (await db.execute(select(Part).where(Part.id == it.part_id))).scalar_one()
        loc = _validate_location(wh, line.location)
        cost = line.unit_cost if line.unit_cost is not None else it.unit_cost
        before = await _part_total(db, part.id)
        await _move(
            db, part=part, wh=wh, location=loc, kind="receipt", delta=line.quantity, actor=actor,
            unit_cost=cost, document_no=body.document_no or po.number, note=f"rendelés {po.number}",
        )
        if cost is not None:
            _update_avg_cost(part, before, line.quantity, cost)
            part.cost_net = cost
        it.qty_received = round(it.qty_received + line.quantity, 4)
    done = all(it.qty_received + 1e-9 >= it.qty_ordered for it in items.values())
    po.status = "received" if done else "partial"
    if done:
        po.received_at = datetime.now(UTC)
    await record_audit(db, actor=actor, action="parts.po_received", entity_type="parts_po", entity_id=str(po.id), detail={"number": po.number, "lines": len(body.lines), "done": done}, request=request)
    await db.commit()
    return await _po_out(db, po)


# ─── Újrarendelési javaslat ──────────────────────────────────────────────────


class ReorderRow(BaseModel):
    part_id: str
    sku: str
    name: str
    unit: str
    total_qty: float
    min_stock: float
    reorder_qty: float | None
    on_order: float
    suggested_qty: float
    supplier_id: str | None
    supplier_name: str | None
    unit_cost: float | None
    lead_days: int | None


@router.get("/reorder", response_model=list[ReorderRow])
async def reorder_suggestions(db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts"))):
    """Min. alatti cikkek: javasolt mennyiség = célszint (reorder_qty, annak
    hiányában 2 × min) − készlet − úton lévő rendelés; preferált beszállító árral."""
    totals = await _totals(db)
    on_order: dict[uuid.UUID, float] = {}
    rows = (
        await db.execute(
            select(PartsPurchaseItem.part_id, sa_func.sum(PartsPurchaseItem.qty_ordered - PartsPurchaseItem.qty_received))
            .join(PartsPurchaseOrder, PartsPurchaseOrder.id == PartsPurchaseItem.po_id)
            .where(PartsPurchaseOrder.status.in_(("ordered", "partial")))
            .group_by(PartsPurchaseItem.part_id)
        )
    ).all()
    for pid, q in rows:
        on_order[pid] = max(0.0, float(q or 0))
    suppliers = {s.id: s for s in (await db.execute(select(PartsSupplier))).scalars().all()}
    out: list[ReorderRow] = []
    for p in (await db.execute(select(Part).where(Part.is_active.is_(True), Part.min_stock > 0).order_by(Part.name))).scalars().all():
        total = totals.get(p.id, 0.0)
        if total >= p.min_stock:
            continue
        target = p.reorder_qty if p.reorder_qty else p.min_stock * 2
        suggested = round(max(0.0, target - total - on_order.get(p.id, 0.0)), 4)
        sup = suppliers.get(p.supplier_id) if p.supplier_id else None
        cost, lead = await _default_cost(db, p, sup.id if sup else None), (sup.lead_days if sup else None)
        if sup:
            sp = (await db.execute(select(PartsSupplierPrice).where(PartsSupplierPrice.supplier_id == sup.id, PartsSupplierPrice.part_id == p.id))).scalar_one_or_none()
            if sp and sp.lead_days is not None:
                lead = sp.lead_days
            if sp and suggested > 0:
                suggested = max(suggested, sp.moq)
        out.append(ReorderRow(
            part_id=str(p.id), sku=p.sku, name=p.name, unit=p.unit, total_qty=total, min_stock=p.min_stock,
            reorder_qty=p.reorder_qty, on_order=on_order.get(p.id, 0.0), suggested_qty=suggested,
            supplier_id=str(sup.id) if sup else None, supplier_name=sup.name if sup else None, unit_cost=cost, lead_days=lead,
        ))
    return out


@router.post("/reorder/create-orders", response_model=list[POOut], status_code=201)
async def create_orders_from_suggestions(
    request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts")),
):
    """A javaslatból beszállítónként egy-egy piszkozat-rendelés (beszállító
    nélküli cikkek egy közös rendelésbe)."""
    rows = await reorder_suggestions(db, actor)
    groups: dict[str | None, list[ReorderRow]] = {}
    for r in rows:
        if r.suggested_qty > 0:
            groups.setdefault(r.supplier_id, []).append(r)
    out: list[POOut] = []
    for sid, items in groups.items():
        po = PartsPurchaseOrder(number=await _next_po_number(db), supplier_id=uuid.UUID(sid) if sid else None, status="draft",
                                note="újrarendelési javaslatból", created_by=actor.id)
        db.add(po)
        await db.flush()
        await _set_po_items(db, po, [POItemBody(part_id=r.part_id, qty_ordered=r.suggested_qty, unit_cost=r.unit_cost) for r in items])
        await record_audit(db, actor=actor, action="parts.po_created", entity_type="parts_po", entity_id=str(po.id), detail={"number": po.number, "items": len(items), "auto": True}, request=request)
        out.append(po)
    await db.commit()
    return [await _po_out(db, po) for po in out]


# ─── Készletérték ────────────────────────────────────────────────────────────


class ValuationRow(BaseModel):
    part_id: str
    sku: str
    name: str
    unit: str
    quantity: float
    avg_cost: float | None
    value: float


class ValuationOut(BaseModel):
    total_value: float
    total_items: int
    rows: list[ValuationRow]
    by_warehouse: list[dict]


@router.get("/valuation", response_model=ValuationOut)
async def valuation(db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts"))):
    """Készletérték mozgó átlagáron (annak hiányában utolsó beszerzési ár)."""
    totals = await _totals(db)
    parts = (await db.execute(select(Part).order_by(Part.name))).scalars().all()
    rows: list[ValuationRow] = []
    cost_of: dict[uuid.UUID, float] = {}
    for p in parts:
        q = totals.get(p.id, 0.0)
        c = p.avg_cost if p.avg_cost is not None else (p.cost_net or 0.0)
        cost_of[p.id] = float(c)
        if q <= 0:
            continue
        rows.append(ValuationRow(part_id=str(p.id), sku=p.sku, name=p.name, unit=p.unit, quantity=q, avg_cost=c, value=round(q * float(c), 2)))
    by_wh: dict[uuid.UUID, float] = {}
    for s in (await db.execute(select(PartsStock).where(PartsStock.quantity > 0))).scalars().all():
        by_wh[s.warehouse_id] = by_wh.get(s.warehouse_id, 0.0) + s.quantity * cost_of.get(s.part_id, 0.0)
    whs = {w.id: w for w in (await db.execute(select(PartsWarehouse))).scalars().all()}
    return ValuationOut(
        total_value=round(sum(r.value for r in rows), 2), total_items=len(rows), rows=rows,
        by_warehouse=[{"warehouse_id": str(k), "name": whs[k].name if k in whs else "?", "kind": whs[k].kind if k in whs else "", "value": round(v, 2)} for k, v in by_wh.items()],
    )


# ─── Karantén / visszáru / garanciaigény ─────────────────────────────────────


class ReturnBody(BaseModel):
    part_id: str
    warehouse_id: str
    location: str = ""
    quantity: float = Field(gt=0)
    supplier_id: str | None = None
    serial_no: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=512)


class ReturnOut(BaseModel):
    id: str
    part_id: str
    sku: str
    name: str
    unit: str
    quantity: float
    serial_no: str | None
    reason: str | None
    status: str
    outcome_note: str | None
    supplier_id: str | None
    supplier_name: str | None
    warehouse_name: str | None
    location: str | None
    created_at: datetime
    decided_at: datetime | None


async def _return_out(db: AsyncSession, r: PartsReturn) -> ReturnOut:
    p = (await db.execute(select(Part).where(Part.id == r.part_id))).scalar_one()
    sup = (await db.execute(select(PartsSupplier).where(PartsSupplier.id == r.supplier_id))).scalar_one_or_none() if r.supplier_id else None
    wh = (await db.execute(select(PartsWarehouse).where(PartsWarehouse.id == r.warehouse_id))).scalar_one_or_none() if r.warehouse_id else None
    return ReturnOut(
        id=str(r.id), part_id=str(p.id), sku=p.sku, name=p.name, unit=p.unit, quantity=r.quantity, serial_no=r.serial_no,
        reason=r.reason, status=r.status, outcome_note=r.outcome_note, supplier_id=str(sup.id) if sup else None,
        supplier_name=sup.name if sup else None, warehouse_name=wh.name if wh else None, location=r.location,
        created_at=r.created_at, decided_at=r.decided_at,
    )


@router.get("/returns", response_model=list[ReturnOut])
async def list_returns(db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts"))):
    rows = (await db.execute(select(PartsReturn).order_by(PartsReturn.created_at.desc()).limit(300))).scalars().all()
    return [await _return_out(db, r) for r in rows]


@router.post("/returns", response_model=ReturnOut, status_code=201)
async def create_return(body: ReturnBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    """Visszáru / garanciaigény: a tétel `return` mozgással kikerül a
    készletből (tipikusan a karantén-raktárból)."""
    part = await _part_or_404(db, body.part_id)
    wh = await _wh_or_404(db, body.warehouse_id)
    _can_manage(actor, wh)
    loc = _validate_location(wh, body.location)
    sup = await _supplier_or_404(db, body.supplier_id) if body.supplier_id else None
    r = PartsReturn(part_id=part.id, supplier_id=sup.id if sup else None, warehouse_id=wh.id, location=loc,
                    quantity=body.quantity, serial_no=body.serial_no, reason=body.reason, status="open", created_by=actor.id)
    db.add(r)
    await db.flush()
    await _move(db, part=part, wh=wh, location=loc, kind="return", delta=-body.quantity, actor=actor,
                document_no=f"RET-{str(r.id)[:8]}", note=body.reason, serial_no=body.serial_no, unit_cost=part.avg_cost)
    await record_audit(db, actor=actor, action="parts.return_created", entity_type="parts_return", entity_id=str(r.id), detail={"sku": part.sku, "qty": body.quantity}, request=request)
    await db.commit()
    return await _return_out(db, r)


class ReturnStatusBody(BaseModel):
    status: str = Field(pattern="^(sent|credited|replaced|rejected|scrapped)$")
    note: str | None = Field(default=None, max_length=512)
    # replaced: a csere-példány bevételezése ide
    warehouse_id: str | None = None
    location: str = ""


@router.post("/returns/{return_id}/status", response_model=ReturnOut)
async def set_return_status(
    return_id: str, body: ReturnStatusBody, request: Request,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts")),
):
    """Állapot: sent (elküldve) → credited (jóváírva) | replaced (cserélve,
    a csere `receipt` mozgással beérkezik) | rejected (elutasítva — a tétel
    `scrap` marad kint) | scrapped (selejt)."""
    r = (await db.execute(select(PartsReturn).where(PartsReturn.id == _uuid(return_id, "parts.return_not_found")))).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail={"code": "parts.return_not_found"})
    if r.status in ("credited", "replaced", "rejected", "scrapped"):
        raise HTTPException(status_code=422, detail={"code": "parts.return_closed"})
    if body.status == "replaced":
        part = (await db.execute(select(Part).where(Part.id == r.part_id))).scalar_one()
        wh = await _wh_or_404(db, body.warehouse_id or str(r.warehouse_id))
        _can_manage(actor, wh)
        loc = _validate_location(wh, body.location)
        await _move(db, part=part, wh=wh, location=loc, kind="receipt", delta=r.quantity, actor=actor,
                    document_no=f"RET-{str(r.id)[:8]}", note="garanciális csere", unit_cost=part.avg_cost)
    r.status, r.outcome_note = body.status, body.note
    if body.status != "sent":
        r.decided_at = datetime.now(UTC)
    await record_audit(db, actor=actor, action="parts.return_status", entity_type="parts_return", entity_id=str(r.id), detail={"status": body.status}, request=request)
    await db.commit()
    return await _return_out(db, r)


# ─── Kitek (szerviz-szett) ───────────────────────────────────────────────────


class KitItemBody(BaseModel):
    part_id: str
    quantity: float = Field(gt=0)


class KitBody(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool = True
    items: list[KitItemBody] = Field(default_factory=list, max_length=100)


class KitItemOut(BaseModel):
    part_id: str
    sku: str
    name: str
    unit: str
    quantity: float


class KitOut(BaseModel):
    id: str
    sku: str
    name: str
    notes: str | None
    is_active: bool
    items: list[KitItemOut]


async def _kit_out(db: AsyncSession, k: PartsKit) -> KitOut:
    rows = (await db.execute(select(PartsKitItem, Part).join(Part, Part.id == PartsKitItem.part_id).where(PartsKitItem.kit_id == k.id).order_by(Part.name))).all()
    return KitOut(id=str(k.id), sku=k.sku, name=k.name, notes=k.notes, is_active=k.is_active,
                  items=[KitItemOut(part_id=str(p.id), sku=p.sku, name=p.name, unit=p.unit, quantity=it.quantity) for it, p in rows])


async def _kit_or_404(db: AsyncSession, raw: str) -> PartsKit:
    k = (await db.execute(select(PartsKit).where(PartsKit.id == _uuid(raw, "parts.kit_not_found")))).scalar_one_or_none()
    if k is None:
        raise HTTPException(status_code=404, detail={"code": "parts.kit_not_found"})
    return k


@router.get("/kits", response_model=list[KitOut])
async def list_kits(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    """Mindenki látja (a szerelő a munkalapon választ belőle)."""
    return [await _kit_out(db, k) for k in (await db.execute(select(PartsKit).where(PartsKit.is_active.is_(True)).order_by(PartsKit.name))).scalars().all()]


@router.post("/kits", response_model=KitOut, status_code=201)
async def create_kit(body: KitBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    sku = body.sku.strip().upper()
    if (await db.execute(select(PartsKit).where(PartsKit.sku == sku))).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail={"code": "parts.sku_taken"})
    k = PartsKit(sku=sku, name=body.name.strip(), notes=body.notes, is_active=body.is_active)
    db.add(k)
    await db.flush()
    for it in body.items:
        db.add(PartsKitItem(kit_id=k.id, part_id=(await _part_or_404(db, it.part_id)).id, quantity=it.quantity))
    await record_audit(db, actor=actor, action="parts.kit_created", entity_type="parts_kit", entity_id=str(k.id), detail={"sku": sku}, request=request)
    await db.commit()
    return await _kit_out(db, k)


@router.patch("/kits/{kit_id}", response_model=KitOut)
async def update_kit(kit_id: str, body: KitBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    k = await _kit_or_404(db, kit_id)
    sku = body.sku.strip().upper()
    if sku != k.sku and (await db.execute(select(PartsKit).where(PartsKit.sku == sku))).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail={"code": "parts.sku_taken"})
    k.sku, k.name, k.notes, k.is_active = sku, body.name.strip(), body.notes, body.is_active
    for old in (await db.execute(select(PartsKitItem).where(PartsKitItem.kit_id == k.id))).scalars().all():
        await db.delete(old)
    await db.flush()
    for it in body.items:
        db.add(PartsKitItem(kit_id=k.id, part_id=(await _part_or_404(db, it.part_id)).id, quantity=it.quantity))
    await record_audit(db, actor=actor, action="parts.kit_updated", entity_type="parts_kit", entity_id=str(k.id), detail={"sku": sku}, request=request)
    await db.commit()
    return await _kit_out(db, k)


class UseKitBody(BaseModel):
    task_id: str
    kit_id: str
    warehouse_id: str | None = None
    location: str = ""


@router.post("/use-kit", response_model=list[UsedPartOut], status_code=201)
async def use_kit(body: UseKitBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(get_current_user)):
    """Szett beépítése: a kit minden tétele külön `use` mozgás ugyanazokkal a
    szabályokkal, mint az egyes beépítés. Ha bármelyik tétel hiányzik, az
    egész kit visszautasítva (409, a hiányzó cikkszámmal)."""
    kit = await _kit_or_404(db, body.kit_id)
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
    items = (await db.execute(select(PartsKitItem, Part).join(Part, Part.id == PartsKitItem.part_id).where(PartsKitItem.kit_id == kit.id))).all()
    for it, part in items:
        row = (await db.execute(select(PartsStock).where(PartsStock.warehouse_id == wh.id, PartsStock.part_id == part.id, PartsStock.location == loc))).scalar_one_or_none()
        if row is None or row.quantity + 1e-9 < it.quantity:
            raise HTTPException(status_code=409, detail={"code": "parts.insufficient_stock", "sku": part.sku, "available": row.quantity if row else 0, "requested": it.quantity})
    befores = {part.id: await _part_total(db, part.id) for _, part in items}
    for it, part in items:
        await _move(db, part=part, wh=wh, location=loc, kind="use", delta=-it.quantity, actor=actor,
                    unit_cost=part.avg_cost if part.avg_cost is not None else part.cost_net, price_net=part.price_net,
                    worksheet_id=ws.id, note=f"szett: {kit.sku}", asset_id=ws.asset_id)
    await record_audit(db, actor=actor, action="parts.use_kit", entity_type="worksheet", entity_id=str(ws.id), detail={"kit": kit.sku, "serial": ws.serial}, request=request)
    await db.commit()
    for _, part in items:
        await _low_check(db, part, befores[part.id])
    return await used_parts_for_worksheet(db, ws.id)
