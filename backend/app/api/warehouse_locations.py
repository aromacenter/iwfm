"""Tárhelyezett raktár (161. kör): tárhelyek szerkezete, szerkesztése és a
készlet tárhelyenkénti bontása.

Hierarchia: sor (betű vagy szám) → állás (két regál-láb közti rész) → szint
(alulról 1) → fakk (opcionális rekesz). Kód: A-03-2[-1].

Minden végpont a /api/warehouses alatt él; a modul-kapcsoló
`warehouse_locations` (a `warehouse` modulra épül).
"""

from __future__ import annotations

import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.api.warehouse import (
    _can_see,
    _fire_movement,
    _get_product_or_404,
    _get_visible_warehouse,
    _require_manager,
)
from app.db import get_db
from app.models import (
    Product,
    User,
    Warehouse,
    WarehouseLabelTemplate,
    WarehouseLocation,
    WarehouseMovement,
    WarehouseStock,
    WarehouseStockLocation,
)
from app.services.wfm.license import require_module
from app.services.wfm.warehouse_labels import LabelConfig, LabelItem, build_labels_pdf, grid as label_grid
from app.services.wfm.warehouse_locations import (
    EPS,
    get_location_or_404,
    is_located,
    located_add,
    located_rows,
    located_take,
    location_code,
    normalize_row_code,
    trim_located,
)

router = APIRouter(dependencies=[Depends(require_module("warehouse_locations"))])

MAX_BAYS = 99
MAX_LEVELS = 20
MAX_SLOTS = 20
MAX_LOCATIONS = 5000


# ─── Séma ───────────────────────────────────────────────────────────────────


class LocationOut(BaseModel):
    id: str
    row_code: str
    bay: int
    level: int
    slot: int
    code: str
    label: str | None
    is_active: bool
    quantity: float  # összes mennyiség a tárhelyen (vegyes egység — csak jelzés)
    product_count: int


class RowSummary(BaseModel):
    row_code: str
    bays: int
    levels: int
    slots: int
    locations: int
    used: int


class LocationsOut(BaseModel):
    layout: str
    row_scheme: str
    rows: list[RowSummary]
    locations: list[LocationOut]


async def _require_located(db: AsyncSession, warehouse_id: str, actor: User) -> Warehouse:
    wh = await _get_visible_warehouse(db, warehouse_id, actor)
    if not is_located(wh):
        raise HTTPException(status_code=422, detail={"code": "warehouse.not_located"})
    return wh


async def _usage(db: AsyncSession, warehouse_id: uuid.UUID) -> dict[uuid.UUID, tuple[float, int]]:
    rows = (
        await db.execute(
            select(
                WarehouseStockLocation.location_id,
                sa_func.sum(WarehouseStockLocation.quantity),
                sa_func.count(WarehouseStockLocation.id),
            )
            .where(WarehouseStockLocation.warehouse_id == warehouse_id, WarehouseStockLocation.quantity > EPS)
            .group_by(WarehouseStockLocation.location_id)
        )
    ).all()
    return {lid: (float(q or 0), int(c or 0)) for lid, q, c in rows}


def _loc_out(loc: WarehouseLocation, usage: dict) -> LocationOut:
    q, c = usage.get(loc.id, (0.0, 0))
    return LocationOut(
        id=str(loc.id), row_code=loc.row_code, bay=loc.bay, level=loc.level, slot=loc.slot,
        code=loc.code, label=loc.label, is_active=loc.is_active,
        quantity=round(q, 2), product_count=c,
    )


async def _list_locations(db: AsyncSession, wh: Warehouse) -> LocationsOut:
    locs = (
        await db.execute(
            select(WarehouseLocation)
            .where(WarehouseLocation.warehouse_id == wh.id)
            .order_by(WarehouseLocation.row_code, WarehouseLocation.bay, WarehouseLocation.level, WarehouseLocation.slot)
        )
    ).scalars().all()
    usage = await _usage(db, wh.id)
    rows: dict[str, RowSummary] = {}
    for loc in locs:
        r = rows.get(loc.row_code)
        if r is None:
            r = rows[loc.row_code] = RowSummary(row_code=loc.row_code, bays=0, levels=0, slots=0, locations=0, used=0)
        r.bays = max(r.bays, loc.bay)
        r.levels = max(r.levels, loc.level)
        r.slots = max(r.slots, loc.slot)
        r.locations += 1
        if usage.get(loc.id, (0.0, 0))[1] > 0:
            r.used += 1
    return LocationsOut(
        layout=wh.layout, row_scheme=wh.row_scheme,
        rows=sorted(rows.values(), key=lambda r: (len(r.row_code), r.row_code)),
        locations=[_loc_out(lc, usage) for lc in locs],
    )


@router.get("/{warehouse_id}/locations", response_model=LocationsOut)
async def list_locations(
    warehouse_id: str,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """A raktár tárhelyei soronként összegezve + a teljes lista (rácsnézethez)."""
    wh = await _require_located(db, warehouse_id, actor)
    return await _list_locations(db, wh)


# ─── Szerkezet: generálás, szerkesztés, tömeges átrendezés ──────────────────


class GenerateBody(BaseModel):
    row_code: str = Field(min_length=1, max_length=4)
    bays: int = Field(ge=1, le=MAX_BAYS)
    levels: int = Field(ge=1, le=MAX_LEVELS)
    slots: int = Field(default=0, ge=0, le=MAX_SLOTS)  # 0 = nincs fakk


@router.post("/{warehouse_id}/locations/generate", response_model=LocationsOut, status_code=201)
async def generate_locations(
    warehouse_id: str,
    body: GenerateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Egy sor tárhelyeinek legenerálása (állás × szint × fakk). Ami már
    létezik, azt kihagyja — így bővíteni is ezzel lehet (több állás/szint)."""
    _require_manager(actor)
    wh = await _require_located(db, warehouse_id, actor)
    row_code = normalize_row_code(body.row_code, wh.row_scheme)
    existing = {
        (lc.bay, lc.level, lc.slot)
        for lc in (
            await db.execute(
                select(WarehouseLocation).where(
                    WarehouseLocation.warehouse_id == wh.id, WarehouseLocation.row_code == row_code
                )
            )
        ).scalars()
    }
    total = (
        await db.execute(
            select(sa_func.count(WarehouseLocation.id)).where(WarehouseLocation.warehouse_id == wh.id)
        )
    ).scalar_one()
    slots = range(1, body.slots + 1) if body.slots else [0]
    created = 0
    for bay in range(1, body.bays + 1):
        for level in range(1, body.levels + 1):
            for slot in slots:
                if (bay, level, slot) in existing:
                    continue
                if total + created >= MAX_LOCATIONS:
                    raise HTTPException(status_code=422, detail={"code": "warehouse.too_many_locations", "max": MAX_LOCATIONS})
                db.add(WarehouseLocation(
                    warehouse_id=wh.id, row_code=row_code, bay=bay, level=level, slot=slot,
                    code=location_code(row_code, bay, level, slot),
                ))
                created += 1
    await db.flush()
    await record_audit(
        db, actor=actor, action="warehouse.locations_generate", entity_type="warehouse",
        entity_id=str(wh.id), detail={"row": row_code, "bays": body.bays, "levels": body.levels, "slots": body.slots, "created": created},
        request=request,
    )
    await db.commit()
    return await _list_locations(db, wh)


class LocationPatch(BaseModel):
    row_code: str | None = Field(default=None, min_length=1, max_length=4)
    bay: int | None = Field(default=None, ge=1, le=MAX_BAYS)
    level: int | None = Field(default=None, ge=1, le=MAX_LEVELS)
    slot: int | None = Field(default=None, ge=0, le=MAX_SLOTS)
    label: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


async def _assert_free(db: AsyncSession, wh_id: uuid.UUID, row_code: str, bay: int, level: int, slot: int, exclude: uuid.UUID) -> None:
    clash = (
        await db.execute(
            select(WarehouseLocation).where(
                WarehouseLocation.warehouse_id == wh_id, WarehouseLocation.row_code == row_code,
                WarehouseLocation.bay == bay, WarehouseLocation.level == level, WarehouseLocation.slot == slot,
                WarehouseLocation.id != exclude,
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(status_code=409, detail={"code": "warehouse.location_exists", "code_value": clash.code})


@router.patch("/{warehouse_id}/locations/{location_id}", response_model=LocationOut)
async def update_location(
    warehouse_id: str,
    location_id: str,
    body: LocationPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Tárhely átrendezése: a koordináták átírhatók, a kód újragenerálódik, a
    rajta lévő készlet marad (az azonosítóhoz kötődik). 409, ha a cél foglalt."""
    _require_manager(actor)
    wh = await _require_located(db, warehouse_id, actor)
    loc = await get_location_or_404(db, wh.id, location_id)
    row_code = normalize_row_code(body.row_code, wh.row_scheme) if body.row_code is not None else loc.row_code
    bay = body.bay if body.bay is not None else loc.bay
    level = body.level if body.level is not None else loc.level
    slot = body.slot if body.slot is not None else loc.slot
    await _assert_free(db, wh.id, row_code, bay, level, slot, loc.id)
    before = loc.code
    loc.row_code, loc.bay, loc.level, loc.slot = row_code, bay, level, slot
    loc.code = location_code(row_code, bay, level, slot)
    if body.label is not None:
        loc.label = body.label.strip() or None
    if body.is_active is not None:
        loc.is_active = body.is_active
    await record_audit(
        db, actor=actor, action="warehouse.location_update", entity_type="warehouse",
        entity_id=str(wh.id), detail={"from": before, "to": loc.code}, request=request,
    )
    await db.commit()
    usage = await _usage(db, wh.id)
    return _loc_out(loc, usage)


class BulkBody(BaseModel):
    op: str = Field(pattern="^(rename_row|shift_bays|shift_levels|delete_empty)$")
    row_code: str = Field(min_length=1, max_length=4)
    new_row_code: str | None = Field(default=None, max_length=4)  # rename_row
    offset: int | None = Field(default=None, ge=-98, le=98)  # shift_bays / shift_levels


@router.post("/{warehouse_id}/locations/bulk", response_model=LocationsOut)
async def bulk_locations(
    warehouse_id: str,
    body: BulkBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Tömeges átrendezés egy sorra: átnevezés (A → C), állások eltolása
    (regál arrébb ment), szintek eltolása, üres tárhelyek törlése. A
    készlet mindenhol a tárhellyel megy."""
    _require_manager(actor)
    wh = await _require_located(db, warehouse_id, actor)
    row_code = normalize_row_code(body.row_code, wh.row_scheme)
    locs = (
        await db.execute(
            select(WarehouseLocation).where(
                WarehouseLocation.warehouse_id == wh.id, WarehouseLocation.row_code == row_code
            )
        )
    ).scalars().all()
    if not locs:
        raise HTTPException(status_code=404, detail={"code": "warehouse.row_not_found"})
    usage = await _usage(db, wh.id)
    if body.op == "rename_row":
        new_code = normalize_row_code(body.new_row_code or "", wh.row_scheme)
        if new_code != row_code:
            taken = (
                await db.execute(
                    select(sa_func.count(WarehouseLocation.id)).where(
                        WarehouseLocation.warehouse_id == wh.id, WarehouseLocation.row_code == new_code
                    )
                )
            ).scalar_one()
            if taken:
                raise HTTPException(status_code=409, detail={"code": "warehouse.row_exists"})
            for lc in locs:
                lc.row_code = new_code
                lc.code = location_code(new_code, lc.bay, lc.level, lc.slot)
    elif body.op in ("shift_bays", "shift_levels"):
        off = body.offset or 0
        if off == 0:
            raise HTTPException(status_code=422, detail={"code": "warehouse.bad_offset"})
        attr = "bay" if body.op == "shift_bays" else "level"
        limit = MAX_BAYS if attr == "bay" else MAX_LEVELS
        for lc in locs:
            nv = getattr(lc, attr) + off
            if nv < 1 or nv > limit:
                raise HTTPException(status_code=422, detail={"code": "warehouse.shift_out_of_range"})
        # két lépésben, hogy az egyedi kulcsok (pozíció + kód) ne ütközzenek útközben:
        # előbb negatív (biztosan szabad) érték, majd a végleges
        for lc in locs:
            setattr(lc, attr, -getattr(lc, attr))
            lc.code = f"~{lc.id}"
        await db.flush()
        for lc in locs:
            setattr(lc, attr, -getattr(lc, attr) + off)
            lc.code = location_code(lc.row_code, lc.bay, lc.level, lc.slot)
    else:  # delete_empty
        for lc in locs:
            if usage.get(lc.id, (0.0, 0))[1] == 0:
                await db.delete(lc)
    await db.flush()
    await record_audit(
        db, actor=actor, action="warehouse.locations_bulk", entity_type="warehouse",
        entity_id=str(wh.id), detail=body.model_dump(), request=request,
    )
    await db.commit()
    return await _list_locations(db, wh)


@router.delete("/{warehouse_id}/locations/{location_id}")
async def delete_location(
    warehouse_id: str,
    location_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Tárhely törlése — csak üresen (422 warehouse.location_not_empty)."""
    _require_manager(actor)
    wh = await _require_located(db, warehouse_id, actor)
    loc = await get_location_or_404(db, wh.id, location_id)
    usage = await _usage(db, wh.id)
    if usage.get(loc.id, (0.0, 0))[1] > 0:
        raise HTTPException(status_code=422, detail={"code": "warehouse.location_not_empty"})
    await record_audit(
        db, actor=actor, action="warehouse.location_delete", entity_type="warehouse",
        entity_id=str(wh.id), detail={"code": loc.code}, request=request,
    )
    await db.delete(loc)
    await db.commit()
    return {"ok": True}


# ─── Készlet tárhelyenként ──────────────────────────────────────────────────


class PlaceOut(BaseModel):
    location_id: str
    code: str
    quantity: float


class LocatedStockOut(BaseModel):
    product_id: str
    product_name: str
    unit: str
    total: float
    unplaced: float
    places: list[PlaceOut]


async def _located_stock(db: AsyncSession, wh: Warehouse, product_id: uuid.UUID | None = None) -> list[LocatedStockOut]:
    q = (
        select(WarehouseStock, Product)
        .join(Product, Product.id == WarehouseStock.product_id)
        .where(WarehouseStock.warehouse_id == wh.id)
        .order_by(Product.name)
    )
    if product_id is not None:
        q = q.where(WarehouseStock.product_id == product_id)
    rows = (await db.execute(q)).all()
    codes = {
        lc.id: lc.code
        for lc in (await db.execute(select(WarehouseLocation).where(WarehouseLocation.warehouse_id == wh.id))).scalars()
    }
    out: list[LocatedStockOut] = []
    for s, p in rows:
        unplaced = await trim_located(db, wh.id, p.id)  # egyeztetés: ami elment, az a tárhelyekről is lekerül
        places = [
            PlaceOut(location_id=str(r.location_id), code=codes.get(r.location_id, "?"), quantity=round(r.quantity, 3))
            for r in sorted(await located_rows(db, wh.id, p.id), key=lambda r: codes.get(r.location_id, ""))
            if r.quantity > EPS
        ]
        out.append(LocatedStockOut(
            product_id=str(p.id), product_name=p.name, unit=p.unit,
            total=round(s.quantity, 3), unplaced=round(unplaced, 3), places=places,
        ))
    await db.commit()
    return out


@router.get("/{warehouse_id}/locations/stock", response_model=list[LocatedStockOut])
async def located_stock(
    warehouse_id: str,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Termékenként: összes, nem tárhelyezett, és a tárhelyek listája."""
    wh = await _require_located(db, warehouse_id, actor)
    return await _located_stock(db, wh)


class ContentOut(BaseModel):
    product_id: str
    product_name: str
    unit: str
    quantity: float


@router.get("/{warehouse_id}/locations/{location_id}/contents", response_model=list[ContentOut])
async def location_contents(
    warehouse_id: str,
    location_id: str,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Mi van egy adott tárhelyen."""
    wh = await _require_located(db, warehouse_id, actor)
    loc = await get_location_or_404(db, wh.id, location_id)
    rows = (
        await db.execute(
            select(WarehouseStockLocation, Product)
            .join(Product, Product.id == WarehouseStockLocation.product_id)
            .where(WarehouseStockLocation.location_id == loc.id, WarehouseStockLocation.quantity > EPS)
            .order_by(Product.name)
        )
    ).all()
    return [ContentOut(product_id=str(p.id), product_name=p.name, unit=p.unit, quantity=round(r.quantity, 3)) for r, p in rows]


class PlaceBody(BaseModel):
    product_id: str
    location_id: str
    quantity: float = Field(gt=0, le=100_000)


@router.post("/{warehouse_id}/locations/place", response_model=LocatedStockOut)
async def place_stock(
    warehouse_id: str,
    body: PlaceBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Berakás: nem tárhelyezett mennyiség → tárhely. 422, ha nincs ennyi
    szabad (nem tárhelyezett) mennyiség."""
    wh = await _require_located(db, warehouse_id, actor)
    product = await _get_product_or_404(db, body.product_id)
    loc = await get_location_or_404(db, wh.id, body.location_id)
    if not loc.is_active:
        raise HTTPException(status_code=422, detail={"code": "warehouse.location_inactive"})
    unplaced = await trim_located(db, wh.id, product.id)
    if unplaced + EPS < body.quantity:
        raise HTTPException(status_code=422, detail={"code": "warehouse.unplaced_insufficient", "available": round(unplaced, 3)})
    await located_add(db, wh.id, product.id, loc.id, body.quantity)
    db.add(WarehouseMovement(
        warehouse_id=wh.id, product_id=product.id, action="place",
        quantity_delta=body.quantity, location_id=loc.id, actor_user_id=actor.id,
    ))
    await record_audit(
        db, actor=actor, action="warehouse.place", entity_type="warehouse",
        entity_id=str(wh.id), detail={"product": product.name, "qty": body.quantity, "location": loc.code}, request=request,
    )
    await db.commit()
    return (await _located_stock(db, wh, product.id))[0]


class UnplaceBody(BaseModel):
    product_id: str
    location_id: str
    quantity: float = Field(gt=0, le=100_000)


@router.post("/{warehouse_id}/locations/unplace", response_model=LocatedStockOut)
async def unplace_stock(
    warehouse_id: str,
    body: UnplaceBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Levétel tárhelyről a nem tárhelyezett készletbe (pl. kiadás előtt)."""
    wh = await _require_located(db, warehouse_id, actor)
    product = await _get_product_or_404(db, body.product_id)
    loc = await get_location_or_404(db, wh.id, body.location_id)
    await located_take(db, wh.id, product.id, loc.id, body.quantity)
    db.add(WarehouseMovement(
        warehouse_id=wh.id, product_id=product.id, action="unplace",
        quantity_delta=-body.quantity, location_id=loc.id, actor_user_id=actor.id,
    ))
    await record_audit(
        db, actor=actor, action="warehouse.unplace", entity_type="warehouse",
        entity_id=str(wh.id), detail={"product": product.name, "qty": body.quantity, "location": loc.code}, request=request,
    )
    await db.commit()
    return (await _located_stock(db, wh, product.id))[0]


class RelocateBody(BaseModel):
    product_id: str
    from_location_id: str
    to_location_id: str
    quantity: float = Field(gt=0, le=100_000)


@router.post("/{warehouse_id}/locations/relocate", response_model=LocatedStockOut)
async def relocate_stock(
    warehouse_id: str,
    body: RelocateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Áthelyezés tárhelyek között ugyanazon a raktáron belül — az összes
    készlet nem változik, csak a bontás."""
    if body.from_location_id == body.to_location_id:
        raise HTTPException(status_code=422, detail={"code": "warehouse.same_location"})
    wh = await _require_located(db, warehouse_id, actor)
    product = await _get_product_or_404(db, body.product_id)
    src = await get_location_or_404(db, wh.id, body.from_location_id)
    dst = await get_location_or_404(db, wh.id, body.to_location_id)
    if not dst.is_active:
        raise HTTPException(status_code=422, detail={"code": "warehouse.location_inactive"})
    await located_take(db, wh.id, product.id, src.id, body.quantity)
    await located_add(db, wh.id, product.id, dst.id, body.quantity)
    db.add(WarehouseMovement(
        warehouse_id=wh.id, product_id=product.id, action="relocate",
        quantity_delta=body.quantity, location_id=src.id, ref_location_id=dst.id,
        actor_user_id=actor.id,
    ))
    await record_audit(
        db, actor=actor, action="warehouse.relocate", entity_type="warehouse",
        entity_id=str(wh.id), detail={"product": product.name, "qty": body.quantity, "from": src.code, "to": dst.code},
        request=request,
    )
    await db.commit()
    await _fire_movement(
        db, muvelet="tárhely-áthelyezés", warehouse_id=wh.id, product=product,
        qty=body.quantity, actor=actor, reszlet=f"{src.code} → {dst.code}",
    )
    return (await _located_stock(db, wh, product.id))[0]


# ─── Kereső: hol van egy termék ─────────────────────────────────────────────


class LocateOut(BaseModel):
    warehouse_id: str
    warehouse_name: str
    location_id: str | None
    code: str | None  # None = nem tárhelyezett rész
    quantity: float


@router.get("/locate", response_model=list[LocateOut])
async def locate_product(
    product_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """„Hol van az X termék?” — minden látható tárhelyezett raktár tárhelyei,
    plusz a nem tárhelyezett maradék."""
    product = await _get_product_or_404(db, product_id)
    whs = [
        w for w in (await db.execute(select(Warehouse).where(Warehouse.layout == "located"))).scalars()
        if _can_see(w, actor)
    ]
    out: list[LocateOut] = []
    for wh in whs:
        unplaced = await trim_located(db, wh.id, product.id)
        codes = {
            lc.id: lc.code
            for lc in (await db.execute(select(WarehouseLocation).where(WarehouseLocation.warehouse_id == wh.id))).scalars()
        }
        for r in await located_rows(db, wh.id, product.id):
            if r.quantity > EPS:
                out.append(LocateOut(
                    warehouse_id=str(wh.id), warehouse_name=wh.name, location_id=str(r.location_id),
                    code=codes.get(r.location_id), quantity=round(r.quantity, 3),
                ))
        if unplaced > EPS:
            out.append(LocateOut(warehouse_id=str(wh.id), warehouse_name=wh.name, location_id=None, code=None, quantity=round(unplaced, 3)))
    await db.commit()
    return sorted(out, key=lambda x: (x.warehouse_name, x.code or "~"))


# ─── Címkék (QR a tárhely-kóddal) ───────────────────────────────────────────


@router.get("/{warehouse_id}/locations/labels.pdf")
async def location_labels_pdf(
    warehouse_id: str,
    row: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """A4 címkelap a tárhelyekhez (QR = a kód, alatta a kód és a raktár neve).
    `row` megadásával csak egy sor."""
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdf_canvas

    from app.services.wfm.worksheet_pdf import FONT, FONT_BOLD

    wh = await _require_located(db, warehouse_id, actor)
    q = select(WarehouseLocation).where(WarehouseLocation.warehouse_id == wh.id, WarehouseLocation.is_active.is_(True))
    if row:
        q = q.where(WarehouseLocation.row_code == normalize_row_code(row, wh.row_scheme))
    locs = (
        await db.execute(q.order_by(WarehouseLocation.row_code, WarehouseLocation.bay, WarehouseLocation.level, WarehouseLocation.slot))
    ).scalars().all()
    if not locs:
        raise HTTPException(status_code=404, detail={"code": "warehouse.no_locations"})

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    cols, rows_per_page = 3, 8
    label_w, label_h = 63.5 * mm, 33.9 * mm
    margin_x = (page_w - cols * label_w) / 2
    margin_y = (page_h - rows_per_page * label_h) / 2
    for i, loc in enumerate(locs):
        if i and i % (cols * rows_per_page) == 0:
            c.showPage()
        k = i % (cols * rows_per_page)
        x = margin_x + (k % cols) * label_w
        y = page_h - margin_y - (k // cols + 1) * label_h
        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.rect(x, y, label_w, label_h)
        size = 26 * mm
        w = QrCodeWidget(loc.code)
        bx, by, bw, bh = w.getBounds()
        d = Drawing(size, size, transform=[size / (bw - bx), 0, 0, size / (bh - by), 0, 0])
        d.add(w)
        renderPDF.draw(d, c, x + 3 * mm, y + (label_h - size) / 2)
        tx = x + 3 * mm + size + 3 * mm
        c.setFont(FONT_BOLD, 16)
        c.drawString(tx, y + label_h - 12 * mm, loc.code)
        c.setFont(FONT, 8)
        c.drawString(tx, y + label_h - 17 * mm, wh.name[:28])
        c.drawString(tx, y + label_h - 22 * mm, f"sor {loc.row_code} · állás {loc.bay} · szint {loc.level}" + (f" · fakk {loc.slot}" if loc.slot else ""))
        if loc.label:
            c.drawString(tx, y + label_h - 27 * mm, loc.label[:30])
    c.save()
    return Response(
        content=buf.getvalue(), media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="tarhely-cimkek-{wh.name[:20]}.pdf"'},
    )


# ─── Konfigurálható címkék + mentett minták (174. kör) ──────────────────────


class LabelPrintBody(LabelConfig):
    """Nyomtatási kérés: a minta beállításai + melyik tárhelyek, hány példány."""

    location_ids: list[str] | None = None  # None = a raktár (vagy a sor) minden aktív tárhelye
    row: str | None = Field(default=None, max_length=4)
    copies: int = Field(default=1, ge=1, le=20)


def _label_link(wh: Warehouse, code: str) -> str:
    from urllib.parse import quote

    from app.core.config import get_settings

    origin = get_settings().frontend_origin.rstrip("/")
    return f"{origin}/raktar?wh={wh.id}&loc={quote(code)}"


@router.post("/{warehouse_id}/locations/labels")
async def location_labels_custom(
    warehouse_id: str,
    body: LabelPrintBody,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Címkék PDF-ben a megadott mintával: egy tárhely, kijelöltek, egy sor
    vagy a teljes raktár; A4/A5/Letter ív vagy tekercs (címkénként egy oldal)."""
    wh = await _require_located(db, warehouse_id, actor)
    if not body.fields:
        raise HTTPException(status_code=422, detail={"code": "warehouse.label_fields"})
    q = select(WarehouseLocation).where(WarehouseLocation.warehouse_id == wh.id)
    if body.location_ids:
        ids: list[uuid.UUID] = []
        for raw in body.location_ids[:MAX_LOCATIONS]:
            try:
                ids.append(uuid.UUID(str(raw)))
            except ValueError:
                continue
        q = q.where(WarehouseLocation.id.in_(ids))
    else:
        q = q.where(WarehouseLocation.is_active.is_(True))
        if body.row:
            q = q.where(WarehouseLocation.row_code == normalize_row_code(body.row, wh.row_scheme))
    locs = (
        await db.execute(q.order_by(WarehouseLocation.row_code, WarehouseLocation.bay, WarehouseLocation.level, WarehouseLocation.slot))
    ).scalars().all()
    if not locs:
        raise HTTPException(status_code=404, detail={"code": "warehouse.no_locations"})
    cfg = LabelConfig(**body.model_dump(include=set(LabelConfig.model_fields)))
    try:
        label_grid(cfg)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "warehouse.label_too_big"}) from None
    items = [
        LabelItem(code=lc.code, row_code=lc.row_code, bay=lc.bay, level=lc.level, slot=lc.slot, note=lc.label, link=_label_link(wh, lc.code))
        for lc in locs
    ]
    pdf = build_labels_pdf(items, cfg, wh.name, copies=body.copies)
    import unicodedata

    ascii_name = unicodedata.normalize("NFKD", wh.name).encode("ascii", "ignore").decode()
    safe = "".join(ch if ch.isalnum() else "-" for ch in ascii_name[:24]).strip("-") or "raktar"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="tarhely-cimkek-{safe}.pdf"'},
    )


class LabelTemplateOut(BaseModel):
    id: str
    name: str
    config: dict
    updated_at: str


class LabelTemplateBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    config: LabelConfig


def _template_out(tp: WarehouseLabelTemplate) -> LabelTemplateOut:
    return LabelTemplateOut(id=str(tp.id), name=tp.name, config=tp.config or {}, updated_at=tp.updated_at.isoformat() if tp.updated_at else "")


@router.get("/label-templates", response_model=list[LabelTemplateOut])
async def list_label_templates(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    rows = (await db.execute(select(WarehouseLabelTemplate).order_by(WarehouseLabelTemplate.name))).scalars().all()
    return [_template_out(tp) for tp in rows]


@router.post("/label-templates", response_model=LabelTemplateOut)
async def save_label_template(
    body: LabelTemplateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Minta mentése névvel; azonos név → felülírás."""
    _require_manager(actor)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail={"code": "warehouse.label_template_name"})
    tp = (await db.execute(select(WarehouseLabelTemplate).where(WarehouseLabelTemplate.name == name))).scalar_one_or_none()
    created = tp is None
    if tp is None:
        tp = WarehouseLabelTemplate(name=name, created_by_user_id=actor.id)
        db.add(tp)
    tp.config = body.config.model_dump()
    await record_audit(
        db, actor=actor, action="warehouse.label_template_save", entity_type="warehouse",
        entity_id=None, detail={"name": name, "created": created}, request=request,
    )
    await db.commit()
    await db.refresh(tp)
    return _template_out(tp)


@router.delete("/label-templates/{template_id}")
async def delete_label_template(
    template_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    _require_manager(actor)
    try:
        tid = uuid.UUID(template_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "not_found"}) from None
    tp = await db.get(WarehouseLabelTemplate, tid)
    if tp is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    await record_audit(
        db, actor=actor, action="warehouse.label_template_delete", entity_type="warehouse",
        entity_id=None, detail={"name": tp.name}, request=request,
    )
    await db.delete(tp)
    await db.commit()
    return {"ok": True}


# ─── Raktár-beállítás: csak-készlet ↔ tárhelyezett ──────────────────────────


class LayoutBody(BaseModel):
    layout: str = Field(pattern="^(simple|located)$")
    row_scheme: str = Field(default="letters", pattern="^(letters|numbers)$")


@router.put("/{warehouse_id}/layout")
async def set_layout(
    warehouse_id: str,
    body: LayoutBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Raktár átállítása tárhelyezettre (vagy vissza). Vissza csak akkor, ha
    egyetlen tárhelyen sincs készlet; a sor-séma csak tárhely nélkül váltható."""
    _require_manager(actor)
    wh = await _get_visible_warehouse(db, warehouse_id, actor)
    if wh.kind == "van" and body.layout == "located":
        raise HTTPException(status_code=422, detail={"code": "warehouse.van_not_located"})
    has_locs = (
        await db.execute(select(sa_func.count(WarehouseLocation.id)).where(WarehouseLocation.warehouse_id == wh.id))
    ).scalar_one()
    if body.row_scheme != wh.row_scheme and has_locs:
        raise HTTPException(status_code=422, detail={"code": "warehouse.scheme_locked"})
    if body.layout == "simple" and wh.layout == "located":
        used = (
            await db.execute(
                select(sa_func.count(WarehouseStockLocation.id)).where(
                    WarehouseStockLocation.warehouse_id == wh.id, WarehouseStockLocation.quantity > EPS
                )
            )
        ).scalar_one()
        if used:
            raise HTTPException(status_code=422, detail={"code": "warehouse.locations_in_use"})
    wh.layout = body.layout
    wh.row_scheme = body.row_scheme
    await record_audit(
        db, actor=actor, action="warehouse.layout", entity_type="warehouse",
        entity_id=str(wh.id), detail=body.model_dump(), request=request,
    )
    await db.commit()
    return {"ok": True, "layout": wh.layout, "row_scheme": wh.row_scheme}
