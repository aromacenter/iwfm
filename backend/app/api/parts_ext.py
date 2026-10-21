"""Alkatrész-raktár — szerviz-mélység és leltár/riport (ERP 1. és 3. kör,
1d553d2e): gép-szintű beépítési történet + garancia, QR-címkék helykódra és
cikkre, autónkénti min/max + feltöltőlista, tömeges kérés, leltár-
munkamenetek (vak leltár, ABC-hatókör, okkódok, jóváhagyás), ABC-besorolás,
fogyás- és készlet-egészség riportok. Ugyanaz a /api/parts prefix és `parts`
modulkapcsoló, mint a parts.py."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, record_audit, require_perm
from app.api.parts import (
    _can_manage, _has_perm, _low_check, _move, _own_vehicle, _part_or_404, _part_total, _stock_row, _totals, _uuid,
    _validate_location, _wh_or_404,
)
from app.db import get_db
from app.models import (
    Asset, Employee, Part, PartsMovement, PartsRequest, PartsStock, PartsStocktake, PartsVehicleLevel, PartsWarehouse,
    Partner, User, Worksheet,
)
from app.services.wfm.license import require_module
from app.services.wfm.telegram import notify_event

router = APIRouter(dependencies=[Depends(require_module("parts"))])


# ─── Gép-szintű beépítési történet + garancia ────────────────────────────────


class InstalledPartOut(BaseModel):
    movement_id: str
    created_at: datetime
    part_id: str
    sku: str
    name: str
    unit: str
    quantity: float
    serial_no: str | None
    warranty_until: date | None
    warranty_active: bool
    worksheet_id: str | None
    worksheet_serial: str | None
    actor_name: str | None
    asset_id: str | None = None
    asset_name: str | None = None
    asset_barcode: str | None = None


async def _installed_rows(db: AsyncSession, where) -> list[InstalledPartOut]:
    rows = (
        await db.execute(
            select(PartsMovement, Part).join(Part, Part.id == PartsMovement.part_id)
            .where(PartsMovement.kind == "use", *where).order_by(PartsMovement.created_at.desc()).limit(1000)
        )
    ).all()
    reversed_ids = {m.document_no for m, _ in rows if m.quantity_delta > 0 and m.document_no}
    live = [(m, p) for m, p in rows if m.quantity_delta < 0 and str(m.id) not in reversed_ids]
    ws_ids = {m.worksheet_id for m, _ in live if m.worksheet_id}
    serials = {w.id: w.serial for w in (await db.execute(select(Worksheet).where(Worksheet.id.in_(ws_ids)))).scalars().all()} if ws_ids else {}
    uids = {m.actor_user_id for m, _ in live if m.actor_user_id}
    names = {u.id: (u.display_name or u.email or "") for u in (await db.execute(select(User).where(User.id.in_(uids)))).scalars().all()} if uids else {}
    aids = {m.asset_id for m, _ in live if m.asset_id}
    assets = {a.id: a for a in (await db.execute(select(Asset).where(Asset.id.in_(aids)))).scalars().all()} if aids else {}
    today = date.today()
    return [
        InstalledPartOut(
            movement_id=str(m.id), created_at=m.created_at, part_id=str(p.id), sku=p.sku, name=p.name, unit=p.unit,
            quantity=round(-m.quantity_delta, 4), serial_no=m.serial_no, warranty_until=m.warranty_until,
            warranty_active=bool(m.warranty_until and m.warranty_until >= today),
            worksheet_id=str(m.worksheet_id) if m.worksheet_id else None, worksheet_serial=serials.get(m.worksheet_id),
            actor_name=names.get(m.actor_user_id) if m.actor_user_id else None,
            asset_id=str(m.asset_id) if m.asset_id else None,
            asset_name=assets[m.asset_id].name if m.asset_id in assets else None,
            asset_barcode=assets[m.asset_id].barcode if m.asset_id in assets else None,
        )
        for m, p in live
    ]


@router.get("/asset/{asset_id}/history", response_model=list[InstalledPartOut])
async def asset_parts_history(asset_id: str, db: AsyncSession = Depends(get_db), actor: User = Depends(get_current_user)):
    """A gépbe eddig beépített alkatrészek (sorozatszám, garancia, munkalap)."""
    if not (await _has_perm(db, actor, "machines") or await _has_perm(db, actor, "parts") or await _has_perm(db, actor, "service")):
        raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
    return await _installed_rows(db, [PartsMovement.asset_id == _uuid(asset_id, "assets.not_found")])


@router.get("/warranty", response_model=list[InstalledPartOut])
async def warranty_expiring(
    days: int = Query(default=30, ge=0, le=730), include_expired: bool = False,
    db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts")),
):
    """Garanciális beépítések, amelyek `days` napon belül lejárnak (gép szerint)."""
    today = date.today()
    cond = [PartsMovement.warranty_until.is_not(None), PartsMovement.warranty_until <= today + timedelta(days=days)]
    if not include_expired:
        cond.append(PartsMovement.warranty_until >= today)
    rows = await _installed_rows(db, cond)
    return sorted(rows, key=lambda r: (r.warranty_until or today))


# ─── QR-címkék helykódra és cikkre ───────────────────────────────────────────


def _labels_pdf(items: list[dict]) -> bytes:
    """51 × 25 mm címkék: QR + nagy betűs kód + megnevezés. Payload:
    PL|<warehouse_id>|<helykód> (hely) / PP|<SKU> (cikk)."""
    import io as _io

    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdf_canvas

    from app.services.wfm.qr_label import SMALL_H, SMALL_W, _draw_qr
    from app.services.wfm.worksheet_pdf import FONT, FONT_BOLD

    buf = _io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(SMALL_W, SMALL_H))
    for i, it in enumerate(items):
        if i > 0:
            c.showPage()
            c.setPageSize((SMALL_W, SMALL_H))
        pad = 1.5 * mm
        qr = SMALL_H - 2 * pad
        _draw_qr(c, it["payload"], pad, pad, qr)
        x = pad + qr + 2 * mm
        max_w = SMALL_W - x - pad
        c.setFont(FONT_BOLD, 13)
        c.drawString(x, SMALL_H - pad - 5 * mm, str(it["code"])[:16])
        c.setFont(FONT, 7)
        name = str(it.get("name") or "")
        while name and c.stringWidth(name, FONT, 7) > max_w:
            name = name[:-1]
        c.drawString(x, SMALL_H - pad - 9.5 * mm, name)
        sub = str(it.get("sub") or "")
        while sub and c.stringWidth(sub, FONT, 7) > max_w:
            sub = sub[:-1]
        c.drawString(x, SMALL_H - pad - 13 * mm, sub)
        c.setFont(FONT, 6)
        c.drawString(x, pad + 1.5 * mm, "Olvassa be a QR-kódot!")
    c.showPage()
    c.save()
    return buf.getvalue()


@router.get("/labels.pdf")
async def labels_pdf(
    warehouse_id: str | None = None,
    parts: str | None = Query(default=None, description="vesszővel elválasztott part_id-k vagy 'all'"),
    db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts")),
):
    """QR-címkék: a raktár minden helykódja (warehouse_id) és/vagy cikkek."""
    items: list[dict] = []
    if warehouse_id:
        wh = await _wh_or_404(db, warehouse_id)
        for col in wh.columns or []:
            for r in range(1, wh.rows + 1):
                items.append({"payload": f"PL|{wh.id}|{col}-{r}", "code": f"{col}-{r}", "name": wh.name, "sub": "helykód"})
    if parts:
        q = select(Part).where(Part.is_active.is_(True)).order_by(Part.sku)
        if parts != "all":
            ids = [_uuid(x.strip(), "parts.not_found") for x in parts.split(",") if x.strip()]
            q = q.where(Part.id.in_(ids))
        for p in (await db.execute(q)).scalars().all():
            items.append({"payload": f"PP|{p.sku}", "code": p.sku, "name": p.name, "sub": p.manufacturer_code or ""})
    if not items:
        raise HTTPException(status_code=422, detail={"code": "parts.labels_empty"})
    return Response(
        content=_labels_pdf(items[:2000]), media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="alkatresz_cimkek.pdf"'},
    )


class ScanOut(BaseModel):
    kind: str  # part | location
    part_id: str | None = None
    sku: str | None = None
    name: str | None = None
    warehouse_id: str | None = None
    warehouse_name: str | None = None
    location: str | None = None


@router.get("/scan", response_model=ScanOut)
async def resolve_scan(code: str = Query(min_length=1, max_length=200), db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    """Beolvasott QR/vonalkód feloldása: PP|SKU → cikk, PL|wh|loc → helykód,
    egyébként SKU / gyártói kód egyezés."""
    code = code.strip()
    if code.startswith("PL|"):
        _, wid, loc = (code.split("|", 2) + ["", ""])[:3]
        wh = await _wh_or_404(db, wid)
        return ScanOut(kind="location", warehouse_id=str(wh.id), warehouse_name=wh.name, location=_validate_location(wh, loc))
    sku = code[3:] if code.startswith("PP|") else code
    p = (await db.execute(select(Part).where((Part.sku == sku.upper()) | (Part.manufacturer_code == sku)))).scalars().first()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "parts.not_found"})
    return ScanOut(kind="part", part_id=str(p.id), sku=p.sku, name=p.name)


# ─── Autónkénti min/max + feltöltőlista ──────────────────────────────────────


class LevelBody(BaseModel):
    part_id: str
    min_qty: float = Field(ge=0)
    max_qty: float = Field(ge=0)


class LevelOut(BaseModel):
    part_id: str
    sku: str
    name: str
    unit: str
    min_qty: float
    max_qty: float
    quantity: float
    suggested: float  # feltöltendő (max − készlet, ha készlet < min)


async def _levels_out(db: AsyncSession, wh: PartsWarehouse) -> list[LevelOut]:
    rows = (await db.execute(select(PartsVehicleLevel, Part).join(Part, Part.id == PartsVehicleLevel.part_id).where(PartsVehicleLevel.warehouse_id == wh.id).order_by(Part.name))).all()
    stock = {s.part_id: s.quantity for s in (await db.execute(select(PartsStock).where(PartsStock.warehouse_id == wh.id))).scalars().all()}
    out = []
    for lv, p in rows:
        q = stock.get(p.id, 0.0)
        out.append(LevelOut(part_id=str(p.id), sku=p.sku, name=p.name, unit=p.unit, min_qty=lv.min_qty, max_qty=lv.max_qty,
                            quantity=q, suggested=round(max(0.0, lv.max_qty - q), 4) if q < lv.min_qty else 0.0))
    return out


@router.get("/warehouses/{warehouse_id}/levels", response_model=list[LevelOut])
async def get_levels(warehouse_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts"))):
    return await _levels_out(db, await _wh_or_404(db, warehouse_id))


@router.put("/warehouses/{warehouse_id}/levels", response_model=list[LevelOut])
async def put_levels(
    warehouse_id: str, body: list[LevelBody], request: Request,
    db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts")),
):
    """A raktár min/max sablonja (teljes csere)."""
    wh = await _wh_or_404(db, warehouse_id)
    for old in (await db.execute(select(PartsVehicleLevel).where(PartsVehicleLevel.warehouse_id == wh.id))).scalars().all():
        await db.delete(old)
    await db.flush()
    seen: set[uuid.UUID] = set()
    for lv in body[:500]:
        part = await _part_or_404(db, lv.part_id)
        if part.id in seen:
            continue
        seen.add(part.id)
        db.add(PartsVehicleLevel(warehouse_id=wh.id, part_id=part.id, min_qty=lv.min_qty, max_qty=max(lv.max_qty, lv.min_qty)))
    await record_audit(db, actor=actor, action="parts.levels_updated", entity_type="parts_warehouse", entity_id=str(wh.id), detail={"rows": len(seen)}, request=request)
    await db.commit()
    return await _levels_out(db, wh)


@router.get("/my-replenish", response_model=list[LevelOut])
async def my_replenish(db: AsyncSession = Depends(get_db), actor: User = Depends(get_current_user)):
    """A szerelő feltöltőlistája a saját autójára (min alatti sorok)."""
    wh = await _own_vehicle(db, actor.id)
    if wh is None:
        return []
    return [r for r in await _levels_out(db, wh) if r.suggested > 0]


class BulkRequestItem(BaseModel):
    part_id: str
    quantity: float = Field(gt=0)


class BulkRequestBody(BaseModel):
    items: list[BulkRequestItem] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=512)


@router.post("/requests/bulk", status_code=201)
async def bulk_requests(body: BulkRequestBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(get_current_user)):
    """Több kiadás-kérés egyszerre (feltöltőlistából) — egy Telegram-üzenettel."""
    wh = await _own_vehicle(db, actor.id)
    if wh is None:
        raise HTTPException(status_code=422, detail={"code": "parts.no_vehicle"})
    created = 0
    names: list[str] = []
    for it in body.items:
        part = await _part_or_404(db, it.part_id)
        if not part.is_active:
            continue
        db.add(PartsRequest(part_id=part.id, requester_user_id=actor.id, warehouse_id=wh.id, quantity=it.quantity,
                            note=(body.note or "").strip() or "feltöltőlista", status="pending"))
        created += 1
        names.append(f"{part.sku}×{it.quantity:g}")
    await record_audit(db, actor=actor, action="parts.request_created", entity_type="parts_request", entity_id=None, detail={"bulk": created}, request=request)
    await db.commit()
    if created:
        try:
            await notify_event(db, "parts.request", {
                "nev": "feltöltőlista", "cikkszam": ", ".join(names[:10]) + (" …" if len(names) > 10 else ""),
                "mennyiseg": str(created), "egyseg": "tétel", "kero": actor.display_name or actor.email, "megjegyzes": "",
            })
        except Exception:  # pragma: no cover
            pass
    return {"created": created}


# ─── Leltár-munkamenetek ─────────────────────────────────────────────────────


class StocktakeCreate(BaseModel):
    warehouse_id: str
    name: str = Field(min_length=1, max_length=128)
    blind: bool = True
    scope: str = Field(default="all", pattern="^(all|A|B|C)$")
    threshold_pct: float = Field(default=10.0, ge=0, le=100)


class StocktakeRowOut(BaseModel):
    part_id: str
    sku: str
    name: str
    unit: str
    location: str
    expected: float | None  # vak leltárnál számolás közben rejtve
    counted: float | None
    delta: float | None
    over_threshold: bool
    reason: str | None


class StocktakeOut(BaseModel):
    id: str
    warehouse_id: str
    warehouse_name: str
    name: str
    blind: bool
    scope: str
    status: str
    threshold_pct: float
    created_at: datetime
    submitted_at: datetime | None
    approved_at: datetime | None
    total_rows: int
    counted_rows: int
    diff_rows: int
    rows: list[StocktakeRowOut] = []


def _st_rows(st: PartsStocktake, hide_expected: bool) -> list[StocktakeRowOut]:
    out = []
    for r in st.rows or []:
        exp, cnt = r.get("expected"), r.get("counted")
        delta = round(cnt - exp, 4) if (cnt is not None and exp is not None and not hide_expected) else None
        over = bool(delta is not None and exp is not None and (abs(delta) > (abs(exp) * st.threshold_pct / 100.0) if exp else delta != 0))
        out.append(StocktakeRowOut(
            part_id=r["part_id"], sku=r.get("sku", ""), name=r.get("name", ""), unit=r.get("unit", "db"), location=r.get("location", ""),
            expected=None if hide_expected else exp, counted=cnt, delta=delta, over_threshold=over, reason=r.get("reason"),
        ))
    return out


async def _st_out(db: AsyncSession, st: PartsStocktake, with_rows: bool = True) -> StocktakeOut:
    wh = (await db.execute(select(PartsWarehouse).where(PartsWarehouse.id == st.warehouse_id))).scalar_one()
    hide = st.blind and st.status == "open"
    rows = _st_rows(st, hide) if with_rows else []
    all_rows = _st_rows(st, False)
    return StocktakeOut(
        id=str(st.id), warehouse_id=str(wh.id), warehouse_name=wh.name, name=st.name, blind=st.blind, scope=st.scope,
        status=st.status, threshold_pct=st.threshold_pct, created_at=st.created_at, submitted_at=st.submitted_at,
        approved_at=st.approved_at, total_rows=len(all_rows), counted_rows=sum(1 for r in all_rows if r.counted is not None),
        diff_rows=sum(1 for r in all_rows if r.delta not in (None, 0)), rows=rows,
    )


async def _st_or_404(db: AsyncSession, raw: str) -> PartsStocktake:
    st = (await db.execute(select(PartsStocktake).where(PartsStocktake.id == _uuid(raw, "parts.stocktake_not_found")))).scalar_one_or_none()
    if st is None:
        raise HTTPException(status_code=404, detail={"code": "parts.stocktake_not_found"})
    return st


@router.get("/stocktakes", response_model=list[StocktakeOut])
async def list_stocktakes(status: str | None = None, db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts"))):
    q = select(PartsStocktake).order_by(PartsStocktake.created_at.desc()).limit(100)
    if status:
        q = q.where(PartsStocktake.status == status)
    return [await _st_out(db, st, with_rows=False) for st in (await db.execute(q)).scalars().all()]


@router.post("/stocktakes", response_model=StocktakeOut, status_code=201)
async def create_stocktake(body: StocktakeCreate, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    """Leltár-munkamenet nyitása: a raktár nyilvántartott sorai (hatókör:
    minden / egy ABC-osztály) pillanatképként; vak leltárnál a számoló nem
    látja a nyilvántartott mennyiséget."""
    wh = await _wh_or_404(db, body.warehouse_id)
    _can_manage(actor, wh)
    q = select(PartsStock, Part).join(Part, Part.id == PartsStock.part_id).where(PartsStock.warehouse_id == wh.id, PartsStock.quantity > 0).order_by(PartsStock.location, Part.name)
    if body.scope in ("A", "B", "C"):
        q = q.where(Part.abc_class == body.scope)
    rows = [
        {"part_id": str(p.id), "sku": p.sku, "name": p.name, "unit": p.unit, "location": s.location, "expected": s.quantity, "counted": None, "reason": None}
        for s, p in (await db.execute(q)).all()
    ]
    if not rows:
        raise HTTPException(status_code=422, detail={"code": "parts.stocktake_empty"})
    st = PartsStocktake(warehouse_id=wh.id, name=body.name.strip(), blind=body.blind, scope=body.scope, status="open",
                        threshold_pct=body.threshold_pct, rows=rows, created_by=actor.id)
    db.add(st)
    await db.flush()
    await record_audit(db, actor=actor, action="parts.stocktake_opened", entity_type="parts_stocktake", entity_id=str(st.id), detail={"name": st.name, "rows": len(rows), "blind": st.blind}, request=request)
    await db.commit()
    return await _st_out(db, st)


@router.get("/stocktakes/{stocktake_id}", response_model=StocktakeOut)
async def get_stocktake(stocktake_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts"))):
    return await _st_out(db, await _st_or_404(db, stocktake_id))


class StocktakeCount(BaseModel):
    part_id: str
    location: str = ""
    counted: float | None = Field(default=None, ge=0)


class StocktakeCountsBody(BaseModel):
    counts: list[StocktakeCount] = Field(min_length=1, max_length=2000)


@router.put("/stocktakes/{stocktake_id}/counts", response_model=StocktakeOut)
async def put_counts(stocktake_id: str, body: StocktakeCountsBody, db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts"))):
    st = await _st_or_404(db, stocktake_id)
    if st.status != "open":
        raise HTTPException(status_code=422, detail={"code": "parts.stocktake_closed"})
    by = {(c.part_id, (c.location or "").strip().upper()): c.counted for c in body.counts}
    rows = [dict(r) for r in (st.rows or [])]
    for r in rows:
        key = (r["part_id"], r.get("location", ""))
        if key in by:
            r["counted"] = by[key]
    st.rows = rows
    await db.commit()
    return await _st_out(db, st)


@router.post("/stocktakes/{stocktake_id}/submit", response_model=StocktakeOut)
async def submit_stocktake(stocktake_id: str, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    """Beküldés: a nem számolt sorok a nyilvántartott értéket kapják; a
    különbségek láthatóvá válnak (vak leltárnál is), jóváhagyásra várnak."""
    st = await _st_or_404(db, stocktake_id)
    if st.status != "open":
        raise HTTPException(status_code=422, detail={"code": "parts.stocktake_closed"})
    rows = [dict(r) for r in (st.rows or [])]
    for r in rows:
        if r.get("counted") is None:
            r["counted"] = r.get("expected")
    st.rows, st.status, st.submitted_at = rows, "submitted", datetime.now(UTC)
    await record_audit(db, actor=actor, action="parts.stocktake_submitted", entity_type="parts_stocktake", entity_id=str(st.id), detail={"name": st.name}, request=request)
    await db.commit()
    return await _st_out(db, st)


class ApproveRow(BaseModel):
    part_id: str
    location: str = ""
    reason: str = Field(pattern="^(count_error|damage|theft|expired|other)$")


class ApproveBody(BaseModel):
    reasons: list[ApproveRow] = Field(default_factory=list, max_length=2000)
    note: str | None = Field(default=None, max_length=512)


@router.post("/stocktakes/{stocktake_id}/approve", response_model=StocktakeOut)
async def approve_stocktake(stocktake_id: str, body: ApproveBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    """Jóváhagyás: az eltérő sorok `adjust` mozgást kapnak okkóddal (alapból
    count_error) — a korrekció a JELENLEGI nyilvántartáshoz képest készül."""
    st = await _st_or_404(db, stocktake_id)
    if st.status != "submitted":
        raise HTTPException(status_code=422, detail={"code": "parts.stocktake_not_submitted"})
    wh = await _wh_or_404(db, str(st.warehouse_id))
    _can_manage(actor, wh)
    reasons = {(r.part_id, (r.location or "").strip().upper()): r.reason for r in body.reasons}
    rows = [dict(r) for r in (st.rows or [])]
    adjusted = 0
    lowered: list[tuple[Part, float]] = []
    note = f"leltár: {st.name}" + (f" — {body.note.strip()}" if body.note and body.note.strip() else "")
    for r in rows:
        cnt = r.get("counted")
        if cnt is None:
            continue
        part = await _part_or_404(db, r["part_id"])
        loc = r.get("location", "")
        row = await _stock_row(db, wh.id, part.id, loc)
        delta = round(float(cnt) - row.quantity, 4)
        if abs(delta) < 1e-9:
            continue
        reason = reasons.get((r["part_id"], loc), r.get("reason") or "count_error")
        r["reason"] = reason
        if delta < 0:
            lowered.append((part, await _part_total(db, part.id)))
        await _move(db, part=part, wh=wh, location=loc, kind="adjust", delta=delta, actor=actor, note=note, reason=reason)
        adjusted += 1
    st.rows, st.status, st.approved_by, st.approved_at = rows, "approved", actor.id, datetime.now(UTC)
    await record_audit(db, actor=actor, action="parts.stocktake_approved", entity_type="parts_stocktake", entity_id=str(st.id), detail={"name": st.name, "adjusted": adjusted}, request=request)
    await db.commit()
    for part, before in lowered:
        await _low_check(db, part, before)
    return await _st_out(db, st)


@router.post("/stocktakes/{stocktake_id}/cancel", response_model=StocktakeOut)
async def cancel_stocktake(stocktake_id: str, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    st = await _st_or_404(db, stocktake_id)
    if st.status == "approved":
        raise HTTPException(status_code=422, detail={"code": "parts.stocktake_closed"})
    st.status = "cancelled"
    await record_audit(db, actor=actor, action="parts.stocktake_cancelled", entity_type="parts_stocktake", entity_id=str(st.id), detail={"name": st.name}, request=request)
    await db.commit()
    return await _st_out(db, st, with_rows=False)


# ─── ABC-besorolás ───────────────────────────────────────────────────────────


@router.post("/abc/recompute")
async def abc_recompute(months: int = Query(default=12, ge=1, le=36), db: AsyncSession = Depends(get_db), actor: User = Depends(require_perm("parts"))):
    """ABC: az elmúlt `months` hónap fogyási értéke (beépítés × önköltség)
    alapján — A: a felső 70 %, B: a következő 20 %, C: a többi (és a nem fogyó)."""
    since = datetime.now(UTC) - timedelta(days=30 * months)
    rows = (
        await db.execute(
            select(PartsMovement.part_id, sa_func.sum(-PartsMovement.quantity_delta * sa_func.coalesce(PartsMovement.unit_cost, 0.0)))
            .where(PartsMovement.kind == "use", PartsMovement.created_at >= since).group_by(PartsMovement.part_id)
        )
    ).all()
    value = {pid: max(0.0, float(v or 0)) for pid, v in rows}
    parts = (await db.execute(select(Part))).scalars().all()
    total = sum(value.values()) or 0.0
    ranked = sorted(parts, key=lambda p: value.get(p.id, 0.0), reverse=True)
    cum = 0.0
    counts = {"A": 0, "B": 0, "C": 0}
    for p in ranked:
        v = value.get(p.id, 0.0)
        if total <= 0 or v <= 0:
            cls = "C"
        else:
            # az osztály a cikk ELŐTTI halmozott arány szerint (így az első, legnagyobb cikk mindig A)
            cls = "A" if cum < total * 0.7 else "B" if cum < total * 0.9 else "C"
            cum += v
        p.abc_class = cls
        counts[cls] += 1
    await db.commit()
    return {"months": months, "total_value": round(total, 2), **counts}


# ─── Riportok ────────────────────────────────────────────────────────────────


class ConsumptionRow(BaseModel):
    key: str
    label: str
    sub: str | None = None
    quantity: float
    lines: int
    cost: float
    revenue: float


@router.get("/reports/consumption", response_model=list[ConsumptionRow])
async def report_consumption(
    date_from: date = Query(alias="from"), date_to: date = Query(alias="to"),
    group: str = Query(default="part", pattern="^(part|employee|asset|partner)$"),
    db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts")),
):
    """Fogyás (nettó beépítés, a stornók levonva) az időszakban, csoportosítva
    cikk / szerelő / gép / partner szerint; önköltség és ügyfél-ár összeggel."""
    start = datetime.combine(date_from, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(date_to, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
    rows = (
        await db.execute(
            select(PartsMovement, Part).join(Part, Part.id == PartsMovement.part_id)
            .where(PartsMovement.kind == "use", PartsMovement.created_at >= start, PartsMovement.created_at < end)
        )
    ).all()
    uids = {m.actor_user_id for m, _ in rows if m.actor_user_id}
    emp_names = {e.user_id: f"{e.last_name} {e.first_name}".strip() for e in (await db.execute(select(Employee).where(Employee.user_id.in_(uids)))).scalars().all()} if uids else {}
    user_names = {u.id: (u.display_name or u.email or "") for u in (await db.execute(select(User).where(User.id.in_(uids)))).scalars().all()} if uids else {}
    aids = {m.asset_id for m, _ in rows if m.asset_id}
    assets = {a.id: a for a in (await db.execute(select(Asset).where(Asset.id.in_(aids)))).scalars().all()} if aids else {}
    pids = {a.partner_id for a in assets.values() if a.partner_id}
    partners = {p.id: p for p in (await db.execute(select(Partner).where(Partner.id.in_(pids)))).scalars().all()} if pids else {}
    agg: dict[str, dict] = {}
    for m, p in rows:
        qty = -m.quantity_delta  # storno: pozitív delta → levon
        if group == "part":
            key, label, sub = str(p.id), p.name, p.sku
        elif group == "employee":
            key = str(m.actor_user_id) if m.actor_user_id else "-"
            label, sub = emp_names.get(m.actor_user_id) or user_names.get(m.actor_user_id, "?"), None
        elif group == "asset":
            a = assets.get(m.asset_id) if m.asset_id else None
            key, label, sub = (str(a.id), a.name, a.barcode) if a else ("-", "— (gép nélkül)", None)
        else:
            a = assets.get(m.asset_id) if m.asset_id else None
            pr = partners.get(a.partner_id) if a and a.partner_id else None
            key, label, sub = (str(pr.id), pr.name, None) if pr else ("-", "— (partner nélkül)", None)
        g = agg.setdefault(key, {"label": label, "sub": sub, "quantity": 0.0, "lines": 0, "cost": 0.0, "revenue": 0.0})
        g["quantity"] += qty
        g["lines"] += 1 if qty > 0 else -1
        g["cost"] += qty * float(m.unit_cost or 0.0)
        g["revenue"] += qty * float(m.price_net or 0.0)
    return sorted(
        (ConsumptionRow(key=k, label=v["label"], sub=v["sub"], quantity=round(v["quantity"], 4), lines=max(0, v["lines"]), cost=round(v["cost"], 2), revenue=round(v["revenue"], 2)) for k, v in agg.items() if v["quantity"] > 0),
        key=lambda r: r.cost, reverse=True,
    )


class HealthRow(BaseModel):
    part_id: str
    sku: str
    name: str
    unit: str
    quantity: float
    value: float
    used_90d: float
    used_365d: float
    turnover: float | None  # éves fogyás / készlet
    days_since_receipt: int | None
    days_since_use: int | None
    dead: bool  # készleten van, de 180 napja nem fogyott
    abc_class: str | None


@router.get("/reports/health", response_model=list[HealthRow])
async def report_health(db: AsyncSession = Depends(get_db), _: User = Depends(require_perm("parts"))):
    """Készlet-egészség: forgás, halott készlet, öregedés, érték."""
    now = datetime.now(UTC)
    totals = await _totals(db)
    d90, d365 = now - timedelta(days=90), now - timedelta(days=365)
    used90: dict[uuid.UUID, float] = {}
    used365: dict[uuid.UUID, float] = {}
    for pid, q in (await db.execute(select(PartsMovement.part_id, sa_func.sum(-PartsMovement.quantity_delta)).where(PartsMovement.kind == "use", PartsMovement.created_at >= d90).group_by(PartsMovement.part_id))).all():
        used90[pid] = max(0.0, float(q or 0))
    for pid, q in (await db.execute(select(PartsMovement.part_id, sa_func.sum(-PartsMovement.quantity_delta)).where(PartsMovement.kind == "use", PartsMovement.created_at >= d365).group_by(PartsMovement.part_id))).all():
        used365[pid] = max(0.0, float(q or 0))
    last_receipt = {pid: dt for pid, dt in (await db.execute(select(PartsMovement.part_id, sa_func.max(PartsMovement.created_at)).where(PartsMovement.kind == "receipt").group_by(PartsMovement.part_id))).all()}
    last_use = {pid: dt for pid, dt in (await db.execute(select(PartsMovement.part_id, sa_func.max(PartsMovement.created_at)).where(PartsMovement.kind == "use", PartsMovement.quantity_delta < 0).group_by(PartsMovement.part_id))).all()}

    def _days(dt: datetime | None) -> int | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0, (now - dt).days)

    out = []
    for p in (await db.execute(select(Part).where(Part.is_active.is_(True)).order_by(Part.name))).scalars().all():
        q = totals.get(p.id, 0.0)
        cost = float(p.avg_cost if p.avg_cost is not None else (p.cost_net or 0.0))
        du = _days(last_use.get(p.id))
        out.append(HealthRow(
            part_id=str(p.id), sku=p.sku, name=p.name, unit=p.unit, quantity=q, value=round(q * cost, 2),
            used_90d=used90.get(p.id, 0.0), used_365d=used365.get(p.id, 0.0),
            turnover=round(used365.get(p.id, 0.0) / q, 2) if q > 0 else None,
            days_since_receipt=_days(last_receipt.get(p.id)), days_since_use=du,
            dead=q > 0 and (du is None or du > 180), abc_class=p.abc_class,
        ))
    return out
