"""Kávégépes QR-fizetés (Saferpay) — "saferpay" licenc-modul.

Folyamat: a gép (vagy a teszt-oldal) rendelést nyit → Payment Page
Initialize → a QR a RedirectUrl-t hordozza → a vevő a telefonján fizet →
a Saferpay meghívja a notify URL-t → Assert + Capture → a rendelés "paid".
A gép a /api/pay/{id}/status végpontot kérdezi (a SAJÁT backendünket —
a Saferpay felé pollozni tilos), és fizetve-állapotnál kiadja az italt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, record_audit, require_perm
from app.core.config import get_settings
from app.db import get_db
from app.models import (
    AgentRelease,
    PaymentOrder,
    PaymentProduct,
    User,
    VendingAsset,
    VendingError,
    VendingFile,
    VendingMachine,
    VendingPromo,
    VendingRecipeEdit,
    VendingTelemetry,
)
from app.services.wfm import saferpay_service
from app.services.wfm.license import require_module
from app.services.wfm.saferpay_service import ORDER_TTL_MINUTES, SaferpayError

router = APIRouter()        # /api/saferpay — belépéshez kötött
public_router = APIRouter()  # /api/pay — a vevő telefonja / a gép hívja


class OrderBody(BaseModel):
    # Katalógus-termékkel (product_id) a név és az ár a katalógusból jön —
    # a gép nem árazhat felül. Szabad név+összeg teszteléshez marad.
    product_id: str | None = Field(default=None, max_length=64)
    product_name: str | None = Field(default=None, max_length=256)
    amount_huf: float | None = Field(default=None, gt=0, le=1_000_000)
    machine_code: str | None = Field(default=None, max_length=64)


class OrderOut(BaseModel):
    id: str
    order_no: str
    product_name: str
    amount_huf: float
    status: str
    machine_code: str | None
    payment_means: str | None
    redirect_url: str | None
    qr_path: str | None
    created_at: datetime
    paid_at: datetime | None
    expires_at: datetime | None


def _effective_status(o: PaymentOrder) -> str:
    if (
        o.status == "created" and o.expires_at is not None
        and datetime.now(UTC) > (
            o.expires_at if o.expires_at.tzinfo else o.expires_at.replace(tzinfo=UTC)
        )
    ):
        return "expired"
    return o.status


def _order_out(o: PaymentOrder) -> OrderOut:
    status = _effective_status(o)
    return OrderOut(
        id=str(o.id), order_no=o.order_no, product_name=o.product_name,
        amount_huf=o.amount_huf, status=status, machine_code=o.machine_code,
        payment_means=o.payment_means,
        redirect_url=o.redirect_url if status == "created" else None,
        qr_path=f"/api/pay/{o.id}/qr.svg" if status == "created" else None,
        created_at=o.created_at, paid_at=o.paid_at, expires_at=o.expires_at,
    )


async def _next_order_no(db: AsyncSession) -> str:
    year = datetime.now(UTC).year
    prefix = f"QR-{year}-"
    last = (
        await db.execute(
            select(PaymentOrder.order_no)
            .where(PaymentOrder.order_no.like(f"{prefix}%"))
            .order_by(PaymentOrder.order_no.desc())
            .limit(1)
        )
    ).scalars().first()
    seq = int(last.split("-")[-1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


@router.post(
    "/orders", response_model=OrderOut, status_code=201,
    dependencies=[Depends(require_module("saferpay"))],
)
async def create_order(
    body: OrderBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """Új QR-fizetéses rendelés: Payment Page Initialize + QR-adatok."""
    if body.product_id:
        try:
            pid = uuid.UUID(body.product_id)
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "saferpay.product_not_found"})
        product = (
            await db.execute(
                select(PaymentProduct).where(
                    PaymentProduct.id == pid, PaymentProduct.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=404, detail={"code": "saferpay.product_not_found"})
        name, amount = product.name, product.price_huf
    else:
        if not body.product_name or not body.amount_huf:
            raise HTTPException(status_code=422, detail={"code": "saferpay.missing_product"})
        name, amount = body.product_name.strip(), body.amount_huf
    order = PaymentOrder(
        order_no=await _next_order_no(db),
        product_name=name,
        amount_huf=float(round(amount)),
        machine_code=(body.machine_code or "").strip() or None,
        created_by=actor.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=ORDER_TTL_MINUTES),
    )
    db.add(order)
    await db.flush()

    # A notify/return a frontend-origón megy (a /api-t a Next proxyzza a
    # backendre) — így nem kell külön publikus backend-URL beállítás.
    base = get_settings().frontend_origin.rstrip("/")
    try:
        token, redirect_url = await saferpay_service.initialize_payment(
            db,
            order_no=order.order_no,
            amount_huf=order.amount_huf,
            description=order.product_name,
            return_url=f"{base}/api/pay/{order.id}/return",
            success_notify_url=f"{base}/api/pay/{order.id}/notify/ok",
            fail_notify_url=f"{base}/api/pay/{order.id}/notify/fail",
        )
    except saferpay_service.SaferpayNotConfigured:
        raise HTTPException(status_code=422, detail={"code": "saferpay.not_configured"})
    except SaferpayError as exc:
        order.status = "failed"
        order.error = str(exc)[:512]
        await db.commit()
        raise HTTPException(
            status_code=502, detail={"code": "saferpay.api_error", "message": exc.message}
        )
    order.token = token
    order.redirect_url = redirect_url
    await record_audit(
        db, actor=actor, action="saferpay.order_created", entity_type="payment_order",
        entity_id=order.order_no,
        detail={"product": order.product_name, "amount": order.amount_huf,
                "machine": order.machine_code},
        request=request,
    )
    await db.commit()
    return _order_out(order)


@router.get(
    "/orders", response_model=list[OrderOut],
    dependencies=[Depends(require_module("saferpay"))],
)
async def list_orders(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    rows = (
        await db.execute(
            select(PaymentOrder).order_by(PaymentOrder.created_at.desc()).limit(100)
        )
    ).scalars().all()
    return [_order_out(o) for o in rows]


# ─── Termékek és árak (kávégép-menü) — adminisztráció az X-adminból ─────────


class ProductBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    price_huf: float = Field(gt=0, le=1_000_000)
    machine_code: str | None = Field(default=None, max_length=64)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=9999)


class ProductOut(BaseModel):
    id: str
    name: str
    price_huf: float
    machine_code: str | None
    is_active: bool
    sort_order: int


def _product_out(p: PaymentProduct) -> ProductOut:
    return ProductOut(
        id=str(p.id), name=p.name, price_huf=p.price_huf,
        machine_code=p.machine_code, is_active=p.is_active, sort_order=p.sort_order,
    )


@router.get(
    "/products", response_model=list[ProductOut],
    dependencies=[Depends(require_module("saferpay"))],
)
async def list_products(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    rows = (
        await db.execute(
            select(PaymentProduct).order_by(PaymentProduct.sort_order, PaymentProduct.name)
        )
    ).scalars().all()
    return [_product_out(p) for p in rows]


@router.post(
    "/products", response_model=ProductOut, status_code=201,
    dependencies=[Depends(require_module("saferpay"))],
)
async def create_product(
    body: ProductBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    p = PaymentProduct(
        name=body.name.strip(), price_huf=float(round(body.price_huf)),
        machine_code=(body.machine_code or "").strip() or None,
        is_active=body.is_active, sort_order=body.sort_order,
    )
    db.add(p)
    await record_audit(
        db, actor=actor, action="saferpay.product_created", entity_type="payment_product",
        entity_id=p.name, detail={"price": p.price_huf, "machine": p.machine_code},
        request=request,
    )
    await db.commit()
    return _product_out(p)


async def _product_or_404(db: AsyncSession, product_id: str) -> PaymentProduct:
    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "saferpay.product_not_found"})
    p = (
        await db.execute(select(PaymentProduct).where(PaymentProduct.id == pid))
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "saferpay.product_not_found"})
    return p


@router.put(
    "/products/{product_id}", response_model=ProductOut,
    dependencies=[Depends(require_module("saferpay"))],
)
async def update_product(
    product_id: str,
    body: ProductBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    p = await _product_or_404(db, product_id)
    p.name = body.name.strip()
    p.price_huf = float(round(body.price_huf))
    p.machine_code = (body.machine_code or "").strip() or None
    p.is_active = body.is_active
    p.sort_order = body.sort_order
    await record_audit(
        db, actor=actor, action="saferpay.product_updated", entity_type="payment_product",
        entity_id=p.name, detail={"price": p.price_huf, "active": p.is_active},
        request=request,
    )
    await db.commit()
    return _product_out(p)


@router.delete(
    "/products/{product_id}",
    dependencies=[Depends(require_module("saferpay"))],
)
async def delete_product(
    product_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    p = await _product_or_404(db, product_id)
    await record_audit(
        db, actor=actor, action="saferpay.product_deleted", entity_type="payment_product",
        entity_id=p.name, detail={}, request=request,
    )
    await db.delete(p)
    await db.commit()
    return {"ok": True}


@public_router.get("/menu")
async def machine_menu(machine: str | None = None, db: AsyncSession = Depends(get_db)):
    """A gép menüje: aktív termékek + árak. A gépre szűkített tétel felülírja
    az azonos nevű általánosat."""
    rows = (
        await db.execute(
            select(PaymentProduct)
            .where(PaymentProduct.is_active.is_(True))
            .order_by(PaymentProduct.sort_order, PaymentProduct.name)
        )
    ).scalars().all()
    machine = (machine or "").strip() or None
    out: dict[str, PaymentProduct] = {}
    for p in rows:
        if p.machine_code and p.machine_code != machine:
            continue
        prev = out.get(p.name)
        # gép-specifikus tétel erősebb az általánosnál
        if prev is None or (p.machine_code and not prev.machine_code):
            out[p.name] = p
    return [
        {"id": str(p.id), "name": p.name, "price_huf": p.price_huf}
        for p in out.values()
    ]


# ─── Vending telemetria: gép-nyilvántartás + a gépek jelentései ──────────────


class MachineBody(BaseModel):
    machine_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    note: str | None = Field(default=None, max_length=512)
    is_active: bool = True


class MachineOut(BaseModel):
    id: str
    machine_code: str
    name: str
    note: str | None
    api_key: str | None
    is_active: bool
    last_seen_at: datetime | None
    telemetry: dict | None
    paid_today: int = 0
    revenue_today: float = 0.0
    open_errors: int = 0


def _machine_out(
    m: VendingMachine, paid_today: int = 0, revenue_today: float = 0.0, open_errors: int = 0
) -> MachineOut:
    return MachineOut(
        id=str(m.id), machine_code=m.machine_code, name=m.name, note=m.note,
        api_key=m.api_key, is_active=m.is_active, last_seen_at=m.last_seen_at,
        telemetry=m.telemetry, paid_today=paid_today, revenue_today=revenue_today,
        open_errors=open_errors,
    )


async def _machine_or_404(db: AsyncSession, machine_id: str) -> VendingMachine:
    try:
        mid = uuid.UUID(machine_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "vending.machine_not_found"})
    m = (
        await db.execute(select(VendingMachine).where(VendingMachine.id == mid))
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail={"code": "vending.machine_not_found"})
    return m


@router.get(
    "/machines", response_model=list[MachineOut],
    dependencies=[Depends(require_module("saferpay"))],
)
async def list_machines(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    """Gépek + utolsó telemetria + mai fizetett forgalom gépenként."""
    from sqlalchemy import func as sa_func

    rows = (
        await db.execute(
            select(VendingMachine).order_by(VendingMachine.name)
        )
    ).scalars().all()
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    stats = {
        code: (int(cnt or 0), float(total or 0.0))
        for code, cnt, total in (
            await db.execute(
                select(
                    PaymentOrder.machine_code,
                    sa_func.count(),
                    sa_func.coalesce(sa_func.sum(PaymentOrder.amount_huf), 0.0),
                )
                .where(
                    PaymentOrder.status == "paid",
                    PaymentOrder.created_at >= today_start,
                    PaymentOrder.machine_code.is_not(None),
                )
                .group_by(PaymentOrder.machine_code)
            )
        ).all()
    }
    open_errors = {
        mid: int(n or 0)
        for mid, n in (
            await db.execute(
                select(VendingError.machine_id, sa_func.count())
                .where(VendingError.resolved_at.is_(None))
                .group_by(VendingError.machine_id)
            )
        ).all()
    }
    return [
        _machine_out(m, *(stats.get(m.machine_code, (0, 0.0))), open_errors.get(m.id, 0))
        for m in rows
    ]


@router.post(
    "/machines", response_model=MachineOut, status_code=201,
    dependencies=[Depends(require_module("saferpay"))],
)
async def create_machine(
    body: MachineBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    dupe = (
        await db.execute(
            select(VendingMachine).where(VendingMachine.machine_code == body.machine_code.strip())
        )
    ).scalar_one_or_none()
    if dupe is not None:
        raise HTTPException(status_code=409, detail={"code": "vending.code_taken"})
    m = VendingMachine(
        machine_code=body.machine_code.strip(), name=body.name.strip(),
        note=(body.note or "").strip() or None, is_active=body.is_active,
        api_key=uuid.uuid4().hex,  # ezt kell a gépre felvinni
    )
    db.add(m)
    await record_audit(
        db, actor=actor, action="vending.machine_created", entity_type="vending_machine",
        entity_id=m.machine_code, detail={"name": m.name}, request=request,
    )
    await db.commit()
    return _machine_out(m)


@router.put(
    "/machines/{machine_id}", response_model=MachineOut,
    dependencies=[Depends(require_module("saferpay"))],
)
async def update_machine(
    machine_id: str,
    body: MachineBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    m = await _machine_or_404(db, machine_id)
    if body.machine_code.strip() != m.machine_code:
        dupe = (
            await db.execute(
                select(VendingMachine).where(VendingMachine.machine_code == body.machine_code.strip())
            )
        ).scalar_one_or_none()
        if dupe is not None:
            raise HTTPException(status_code=409, detail={"code": "vending.code_taken"})
    m.machine_code = body.machine_code.strip()
    m.name = body.name.strip()
    m.note = (body.note or "").strip() or None
    m.is_active = body.is_active
    await record_audit(
        db, actor=actor, action="vending.machine_updated", entity_type="vending_machine",
        entity_id=m.machine_code, detail={"name": m.name, "active": m.is_active},
        request=request,
    )
    await db.commit()
    return _machine_out(m)


@router.post(
    "/machines/{machine_id}/regen-key", response_model=MachineOut,
    dependencies=[Depends(require_module("saferpay"))],
)
async def regen_machine_key(
    machine_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    """Új API-kulcs a gépnek — a régi azonnal érvénytelen."""
    m = await _machine_or_404(db, machine_id)
    m.api_key = uuid.uuid4().hex
    await record_audit(
        db, actor=actor, action="vending.machine_key_regen", entity_type="vending_machine",
        entity_id=m.machine_code, detail={}, request=request,
    )
    await db.commit()
    return _machine_out(m)


@router.delete(
    "/machines/{machine_id}",
    dependencies=[Depends(require_module("saferpay"))],
)
async def delete_machine(
    machine_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    m = await _machine_or_404(db, machine_id)
    await record_audit(
        db, actor=actor, action="vending.machine_deleted", entity_type="vending_machine",
        entity_id=m.machine_code, detail={"name": m.name}, request=request,
    )
    await db.delete(m)
    await db.commit()
    return {"ok": True}


@router.get(
    "/machines/{machine_id}/telemetry",
    dependencies=[Depends(require_module("saferpay"))],
)
async def machine_telemetry_history(
    machine_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    """A gép jelentései (legfrissebb elöl) — a rendezett előzmény-nézethez."""
    m = await _machine_or_404(db, machine_id)
    rows = (
        await db.execute(
            select(VendingTelemetry)
            .where(VendingTelemetry.machine_id == m.id)
            .order_by(VendingTelemetry.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [{"created_at": r.created_at, "data": r.data} for r in rows]


FILE_KINDS = ("catalog", "order_sample", "file", "original")
FILES_KEPT_PER_KIND = 10
# az eredeti Product/Recipe fájlokat (original) sosem takarítjuk automatikusan
FILES_KEPT_OVERRIDE = {"original": 1000}


@router.get(
    "/machines/{machine_id}/files",
    dependencies=[Depends(require_module("saferpay"))],
)
async def machine_files(
    machine_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    """A gép által feltöltött fájlok listája (tartalom nélkül)."""
    m = await _machine_or_404(db, machine_id)
    rows = (
        await db.execute(
            select(VendingFile)
            .where(VendingFile.machine_id == m.id)
            .order_by(VendingFile.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [
        {"id": str(r.id), "kind": r.kind, "name": r.name, "size": r.size, "created_at": r.created_at}
        for r in rows
    ]


@router.get(
    "/machines/{machine_id}/files/{file_id}",
    dependencies=[Depends(require_module("saferpay"))],
)
async def machine_file(
    machine_id: str,
    file_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    m = await _machine_or_404(db, machine_id)
    try:
        fid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "vending.file_not_found"})
    r = (
        await db.execute(
            select(VendingFile).where(VendingFile.id == fid, VendingFile.machine_id == m.id)
        )
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail={"code": "vending.file_not_found"})
    return {
        "id": str(r.id), "kind": r.kind, "name": r.name, "size": r.size,
        "created_at": r.created_at, "text": r.text,
    }


@router.get(
    "/machines/{machine_id}/catalog",
    dependencies=[Depends(require_module("saferpay"))],
)
async def machine_catalog(
    machine_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    """A legutóbbi ital-/recept-katalógus (adagok) — az ügynök a gép Product/
    Recipe fájljaiból állítja össze és JSON-ként tölti fel."""
    import json

    m = await _machine_or_404(db, machine_id)
    r = (
        await db.execute(
            select(VendingFile)
            .where(VendingFile.machine_id == m.id, VendingFile.kind == "catalog")
            .order_by(VendingFile.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if r is None:
        return {"updated_at": None, "catalog": None}
    try:
        catalog = json.loads(r.text)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "vending.catalog_invalid"})
    return {"updated_at": r.created_at, "catalog": catalog}


# ─── Vending promóció: reklámképek a gépek MakingImage / ScreenProtect mappájába ─

PROMO_SLOTS = ("making", "screensaver")
PROMO_MAX_BYTES = 3 * 1024 * 1024


def _promo_out(p: VendingPromo) -> dict:
    return {
        "content_type": p.content_type,
        "id": str(p.id), "slot": p.slot, "filename": p.filename, "title": p.title,
        "size": p.size, "width": p.width, "height": p.height, "thumb": p.thumb,
        "sort_order": p.sort_order, "status": p.status, "error": p.error,
        "created_at": p.created_at, "delivered_at": p.delivered_at,
    }


@router.get(
    "/machines/{machine_id}/promos",
    dependencies=[Depends(require_module("saferpay"))],
)
async def list_promos(
    machine_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    m = await _machine_or_404(db, machine_id)
    rows = (
        await db.execute(
            select(VendingPromo)
            .where(VendingPromo.machine_id == m.id)
            .order_by(VendingPromo.slot, VendingPromo.sort_order, VendingPromo.created_at)
        )
    ).scalars().all()
    return {
        "exclusive": {
            "making": m.promo_exclusive_making,
            "screensaver": m.promo_exclusive_screensaver,
        },
        "items": [_promo_out(p) for p in rows],
    }


PROMO_VIDEO_MAX_BYTES = 30 * 1024 * 1024


class PromoBody(BaseModel):
    slot: str = Field(min_length=1, max_length=16)
    title: str = Field(min_length=1, max_length=256)
    data_b64: str = Field(min_length=16, max_length=42_000_000)  # JPEG max 3 MB, MP4 max 30 MB
    thumb: str | None = Field(default=None, max_length=200_000)  # data-URL bélyegkép
    width: int = 0
    height: int = 0
    content_type: str = Field(default="image/jpeg", max_length=64)  # image/jpeg | video/mp4


def _safe_stem(title: str) -> str:
    import re

    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", title.rsplit(".", 1)[0]).strip("_")[:40]
    return stem or "kep"


@router.post(
    "/machines/{machine_id}/promos",
    dependencies=[Depends(require_module("saferpay"))],
)
async def create_promo(
    machine_id: str,
    body: PromoBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("invoicing")),
):
    """Kép feltöltése (a böngésző már 1280×800 JPEG-re méretezte). A gépen
    xadmin_ előtaggal kap nevet, így a gyári képektől megkülönböztethető."""
    import base64
    import hashlib

    from sqlalchemy import func as sa_func

    m = await _machine_or_404(db, machine_id)
    if body.slot not in PROMO_SLOTS:
        raise HTTPException(status_code=422, detail={"code": "vending.bad_slot"})
    try:
        raw = base64.b64decode(body.data_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail={"code": "vending.bad_image"})
    is_video = body.content_type == "video/mp4"
    if is_video:
        # MP4: az 'ftyp' doboz a 4. bájttól; csak a képernyővédő mappába (a MakingImage képeket vár)
        if body.slot != "screensaver":
            raise HTTPException(status_code=422, detail={"code": "vending.video_only_screensaver"})
        if len(raw) > PROMO_VIDEO_MAX_BYTES or raw[4:8] != b"ftyp":
            raise HTTPException(status_code=422, detail={"code": "vending.bad_video"})
    elif len(raw) > PROMO_MAX_BYTES or not raw.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=422, detail={"code": "vending.bad_image"})
    n = int(
        (
            await db.execute(
                select(sa_func.count()).select_from(VendingPromo).where(
                    VendingPromo.machine_id == m.id, VendingPromo.slot == body.slot
                )
            )
        ).scalar_one()
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    filename = f"xadmin_{body.slot}_{stamp}_{_safe_stem(body.title)}_1280x800." + ("mp4" if is_video else "jpg")
    p = VendingPromo(
        machine_id=m.id, slot=body.slot, filename=filename[:128], title=body.title.strip()[:256],
        content_type="video/mp4" if is_video else "image/jpeg", data=raw, sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw), width=body.width, height=body.height, thumb=body.thumb,
        sort_order=n, status="pending",
    )
    db.add(p)
    await record_audit(
        db, actor=user, action="vending.promo_uploaded", entity_type="vending_promo",
        entity_id=m.machine_code, detail={"slot": body.slot, "title": p.title, "size": p.size},
        request=request,
    )
    await db.commit()
    await db.refresh(p)
    return _promo_out(p)


@router.delete(
    "/machines/{machine_id}/promos/{promo_id}",
    dependencies=[Depends(require_module("saferpay"))],
)
async def delete_promo(
    machine_id: str,
    promo_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("invoicing")),
):
    """Ha a kép még nem jutott el a gépre, azonnal törlődik; ha igen, az ügynök
    törli a gépről a következő szinkronnál (remove_pending), majd a sor is megy."""
    m = await _machine_or_404(db, machine_id)
    try:
        pid = uuid.UUID(promo_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "vending.promo_not_found"})
    p = (
        await db.execute(
            select(VendingPromo).where(VendingPromo.id == pid, VendingPromo.machine_id == m.id)
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "vending.promo_not_found"})
    if p.delivered_at is None:
        await db.delete(p)
    else:
        p.status = "remove_pending"
        p.error = None
    await record_audit(
        db, actor=user, action="vending.promo_deleted", entity_type="vending_promo",
        entity_id=m.machine_code, detail={"slot": p.slot, "title": p.title}, request=request,
    )
    await db.commit()
    return {"ok": True}


class PromoSettingsBody(BaseModel):
    exclusive_making: bool
    exclusive_screensaver: bool


@router.put(
    "/machines/{machine_id}/promo-settings",
    dependencies=[Depends(require_module("saferpay"))],
)
async def promo_settings(
    machine_id: str,
    body: PromoSettingsBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    m = await _machine_or_404(db, machine_id)
    m.promo_exclusive_making = body.exclusive_making
    m.promo_exclusive_screensaver = body.exclusive_screensaver
    await db.commit()
    return {"ok": True}


class MachineAuthBody(BaseModel):
    machine_code: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=8, max_length=64)


async def _machine_by_key(db: AsyncSession, body: MachineAuthBody) -> VendingMachine:
    m = (
        await db.execute(
            select(VendingMachine).where(VendingMachine.machine_code == body.machine_code.strip())
        )
    ).scalar_one_or_none()
    if m is None or not m.is_active or not m.api_key or m.api_key != body.key:
        raise HTTPException(status_code=403, detail={"code": "vending.bad_key"})
    return m


@public_router.post("/promos/manifest")
async def promo_manifest(body: MachineAuthBody, db: AsyncSession = Depends(get_db)):
    """A GÉP kérdezi le: mit kell a mappáiba tenni / onnan törölni."""
    m = await _machine_by_key(db, body)
    rows = (
        await db.execute(
            select(VendingPromo)
            .where(VendingPromo.machine_id == m.id)
            .order_by(VendingPromo.slot, VendingPromo.sort_order)
        )
    ).scalars().all()
    m.last_seen_at = datetime.now(UTC)
    await db.commit()
    return {
        "exclusive": {
            "making": m.promo_exclusive_making,
            "screensaver": m.promo_exclusive_screensaver,
        },
        "items": [
            {
                "id": str(p.id), "slot": p.slot, "filename": p.filename, "sha256": p.sha256,
                "size": p.size, "action": "delete" if p.status == "remove_pending" else "put",
            }
            for p in rows
        ],
    }


@public_router.post("/promos/{promo_id}/file")
async def promo_file(promo_id: str, body: MachineAuthBody, db: AsyncSession = Depends(get_db)):
    m = await _machine_by_key(db, body)
    try:
        pid = uuid.UUID(promo_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "vending.promo_not_found"})
    p = (
        await db.execute(
            select(VendingPromo).where(VendingPromo.id == pid, VendingPromo.machine_id == m.id)
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "vending.promo_not_found"})
    return Response(
        content=bytes(p.data), media_type=p.content_type,
        headers={"X-Sha256": p.sha256, "Content-Disposition": f'inline; filename="{p.filename}"'},
    )


class PromoResult(BaseModel):
    id: str
    ok: bool
    error: str | None = Field(default=None, max_length=512)


class PromoReportBody(MachineAuthBody):
    results: list[PromoResult] = Field(default_factory=list, max_length=200)


@public_router.post("/promos/report")
async def promo_report(body: PromoReportBody, db: AsyncSession = Depends(get_db)):
    """A GÉP visszajelez: melyik kép került fel (delivered) / bukott (failed) /
    törlődött (a sor is megy)."""
    m = await _machine_by_key(db, body)
    now = datetime.now(UTC)
    for r in body.results:
        try:
            pid = uuid.UUID(r.id)
        except ValueError:
            continue
        p = (
            await db.execute(
                select(VendingPromo).where(VendingPromo.id == pid, VendingPromo.machine_id == m.id)
            )
        ).scalar_one_or_none()
        if p is None:
            continue
        if p.status == "remove_pending":
            if r.ok:
                await db.delete(p)
            else:
                p.error = (r.error or "törlés sikertelen")[:512]
        elif r.ok:
            p.status, p.error, p.delivered_at = "delivered", None, now
        else:
            p.status, p.error = "failed", (r.error or "ismeretlen hiba")[:512]
    m.last_seen_at = now
    await db.commit()
    return {"ok": True}


# ─── Vending médiafájlok a gépről (gyári képek, ikonok) — lista, ZIP ─────────

ASSET_KINDS = ("making", "screensaver", "product_image", "other")
ASSET_MAX_BYTES = 15 * 1024 * 1024


def _asset_out(a: VendingAsset) -> dict:
    return {
        "id": str(a.id), "kind": a.kind, "name": a.name, "content_type": a.content_type,
        "size": a.size, "sha256": a.sha256, "created_at": a.created_at, "updated_at": a.updated_at,
    }


@public_router.post("/assets")
async def upload_asset(request: Request, db: AsyncSession = Depends(get_db)):
    """A GÉP tölt fel bináris fájlt (nyers törzs; fejlécek: X-Machine-Code,
    X-Key, X-Kind, X-Name, Content-Type). Azonos név → felülírás."""
    import hashlib

    auth = MachineAuthBody(
        machine_code=request.headers.get("x-machine-code", "").strip() or "-",
        key=request.headers.get("x-key", "").strip() or "-" * 8,
    )
    m = await _machine_by_key(db, auth)
    kind = request.headers.get("x-kind", "other").strip()
    name = request.headers.get("x-name", "").strip().replace("/", "_").replace("\\", "_")[:256]
    if kind not in ASSET_KINDS or not name:
        raise HTTPException(status_code=422, detail={"code": "vending.bad_asset"})
    raw = await request.body()
    if not raw or len(raw) > ASSET_MAX_BYTES:
        raise HTTPException(status_code=422, detail={"code": "vending.asset_too_big"})
    sha = hashlib.sha256(raw).hexdigest()
    a = (
        await db.execute(
            select(VendingAsset).where(
                VendingAsset.machine_id == m.id, VendingAsset.kind == kind, VendingAsset.name == name
            )
        )
    ).scalar_one_or_none()
    if a is None:
        a = VendingAsset(machine_id=m.id, kind=kind, name=name)
        db.add(a)
    a.content_type = (request.headers.get("content-type") or "application/octet-stream").split(";")[0][:64]
    a.data = raw
    a.sha256 = sha
    a.size = len(raw)
    m.last_seen_at = datetime.now(UTC)
    await db.commit()
    return {"ok": True, "sha256": sha, "size": len(raw)}


@public_router.post("/assets/manifest")
async def assets_manifest(body: MachineAuthBody, db: AsyncSession = Depends(get_db)):
    """A GÉP kérdezi: mi van már fent (kind/name → sha256), hogy csak az újat küldje."""
    m = await _machine_by_key(db, body)
    rows = (await db.execute(select(VendingAsset).where(VendingAsset.machine_id == m.id))).scalars().all()
    return {"items": [{"kind": a.kind, "name": a.name, "sha256": a.sha256} for a in rows]}


@router.get(
    "/machines/{machine_id}/assets",
    dependencies=[Depends(require_module("saferpay"))],
)
async def list_assets(
    machine_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    m = await _machine_or_404(db, machine_id)
    rows = (
        await db.execute(
            select(VendingAsset).where(VendingAsset.machine_id == m.id).order_by(VendingAsset.kind, VendingAsset.name)
        )
    ).scalars().all()
    return [_asset_out(a) for a in rows]


@router.get(
    "/machines/{machine_id}/assets/{asset_id}",
    dependencies=[Depends(require_module("saferpay"))],
)
async def get_asset(
    machine_id: str,
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    m = await _machine_or_404(db, machine_id)
    try:
        aid = uuid.UUID(asset_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "vending.asset_not_found"})
    a = (
        await db.execute(select(VendingAsset).where(VendingAsset.id == aid, VendingAsset.machine_id == m.id))
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail={"code": "vending.asset_not_found"})
    return Response(
        content=bytes(a.data), media_type=a.content_type,
        headers={"Content-Disposition": f'inline; filename="{a.name}"', "Cache-Control": "private, max-age=3600"},
    )


@router.get(
    "/machines/{machine_id}/assets.zip",
    dependencies=[Depends(require_module("saferpay"))],
)
async def assets_zip(
    machine_id: str,
    kind: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    """A gép médiafájljai egy ZIP-ben (mappánként: making/, screensaver/, product_image/)."""
    import io
    import zipfile

    m = await _machine_or_404(db, machine_id)
    q = select(VendingAsset).where(VendingAsset.machine_id == m.id)
    if kind:
        q = q.where(VendingAsset.kind == kind)
    rows = (await db.execute(q.order_by(VendingAsset.kind, VendingAsset.name))).scalars().all()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in rows:
            z.writestr(f"{a.kind}/{a.name}", bytes(a.data))
    buf.seek(0)
    fname = f"gep-{m.machine_code}-kepek{'-' + kind if kind else ''}.zip"
    return Response(
        content=buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ─── Vending receptek: távoli adag-/ármódosítás, eredeti visszaállítása ──────

RECIPE_LIMITS = {"coffee_g": (0.0, 30.0), "water_ml": (0.0, 500.0), "price": (0.0, 100_000.0)}


def _edit_out(e: VendingRecipeEdit) -> dict:
    return {
        "id": str(e.id), "action": e.action, "product_id": e.product_id,
        "product_name": e.product_name, "recipe_name": e.recipe_name, "price": e.price,
        "steps": e.steps, "status": e.status, "error": e.error,
        "created_at": e.created_at, "applied_at": e.applied_at,
    }


class RecipeStep(BaseModel):
    canister: int
    water_ml: float = Field(ge=0, le=500)
    coffee_g: float = Field(ge=0, le=30)


class RecipeEditBody(BaseModel):
    product_name: str = Field(default="", max_length=256)
    recipe_name: str = Field(min_length=1, max_length=256)
    price: float | None = Field(default=None, ge=0, le=100_000)
    steps: list[RecipeStep] | None = Field(default=None, max_length=12)


@router.get(
    "/machines/{machine_id}/recipes",
    dependencies=[Depends(require_module("saferpay"))],
)
async def list_recipe_edits(
    machine_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    """Módosítás-kérések (függő/alkalmazott/hibás) + van-e lementett eredeti a szerveren."""
    from sqlalchemy import func as sa_func

    m = await _machine_or_404(db, machine_id)
    rows = (
        await db.execute(
            select(VendingRecipeEdit)
            .where(VendingRecipeEdit.machine_id == m.id)
            .order_by(VendingRecipeEdit.created_at.desc())
            .limit(100)
        )
    ).scalars().all()
    originals = int(
        (
            await db.execute(
                select(sa_func.count()).select_from(VendingFile).where(
                    VendingFile.machine_id == m.id, VendingFile.kind == "original"
                )
            )
        ).scalar_one()
    )
    return {"originals_saved": originals > 0, "items": [_edit_out(e) for e in rows]}


@router.put(
    "/machines/{machine_id}/recipes/{product_id}",
    dependencies=[Depends(require_module("saferpay"))],
)
async def set_recipe_edit(
    machine_id: str,
    product_id: int,
    body: RecipeEditBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("invoicing")),
):
    """Adag/ár módosítás egy italra: egy függő kérés italonként (felülírja a
    korábbi függőt). Az ügynök az eredeti fájlt menti, majd a következő
    jelentéskor alkalmazza és visszajelez."""
    m = await _machine_or_404(db, machine_id)
    if body.price is None and body.steps is None:
        raise HTTPException(status_code=422, detail={"code": "vending.recipe_empty"})
    pending = (
        await db.execute(
            select(VendingRecipeEdit).where(
                VendingRecipeEdit.machine_id == m.id,
                VendingRecipeEdit.product_id == product_id,
                VendingRecipeEdit.action == "set",
                VendingRecipeEdit.status == "pending",
            )
        )
    ).scalar_one_or_none()
    e = pending or VendingRecipeEdit(machine_id=m.id, action="set", product_id=product_id)
    e.product_name = body.product_name.strip()[:256]
    e.recipe_name = body.recipe_name.strip()[:256]
    e.price = body.price
    e.steps = [s.model_dump() for s in body.steps] if body.steps is not None else None
    e.status, e.error = "pending", None
    db.add(e)
    await record_audit(
        db, actor=user, action="vending.recipe_edit", entity_type="vending_recipe",
        entity_id=m.machine_code,
        detail={"product": e.product_name, "price": e.price, "steps": e.steps}, request=request,
    )
    await db.commit()
    await db.refresh(e)
    return _edit_out(e)


@router.post(
    "/machines/{machine_id}/recipes/restore",
    dependencies=[Depends(require_module("saferpay"))],
)
async def restore_recipes(
    machine_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("invoicing")),
):
    """Az eredeti (első módosítás előtti) Product/Recipe fájlok visszaállítása
    a gépen — minden függő módosítás-kérés érvényét veszti."""
    m = await _machine_or_404(db, machine_id)
    rows = (
        await db.execute(
            select(VendingRecipeEdit).where(
                VendingRecipeEdit.machine_id == m.id, VendingRecipeEdit.status == "pending"
            )
        )
    ).scalars().all()
    for r in rows:
        await db.delete(r)
    e = VendingRecipeEdit(machine_id=m.id, action="restore", product_id=0, product_name="*", recipe_name="*")
    db.add(e)
    await record_audit(
        db, actor=user, action="vending.recipe_restore", entity_type="vending_recipe",
        entity_id=m.machine_code, detail={}, request=request,
    )
    await db.commit()
    await db.refresh(e)
    return _edit_out(e)


@router.delete(
    "/machines/{machine_id}/recipes/{edit_id}",
    dependencies=[Depends(require_module("saferpay"))],
)
async def delete_recipe_edit(
    machine_id: str,
    edit_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    m = await _machine_or_404(db, machine_id)
    try:
        eid = uuid.UUID(edit_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "vending.edit_not_found"})
    e = (
        await db.execute(
            select(VendingRecipeEdit).where(VendingRecipeEdit.id == eid, VendingRecipeEdit.machine_id == m.id)
        )
    ).scalar_one_or_none()
    if e is None:
        raise HTTPException(status_code=404, detail={"code": "vending.edit_not_found"})
    await db.delete(e)
    await db.commit()
    return {"ok": True}


@public_router.post("/recipes/manifest")
async def recipes_manifest(body: MachineAuthBody, db: AsyncSession = Depends(get_db)):
    """A GÉP kérdezi: függő recept-/ármódosítások (restore előre sorolva)."""
    m = await _machine_by_key(db, body)
    rows = (
        await db.execute(
            select(VendingRecipeEdit)
            .where(VendingRecipeEdit.machine_id == m.id, VendingRecipeEdit.status == "pending")
            .order_by(VendingRecipeEdit.created_at)
        )
    ).scalars().all()
    rows.sort(key=lambda r: 0 if r.action == "restore" else 1)
    return {"items": [_edit_out(e) for e in rows]}


@public_router.post("/recipes/report")
async def recipes_report(body: PromoReportBody, db: AsyncSession = Depends(get_db)):
    m = await _machine_by_key(db, body)
    now = datetime.now(UTC)
    for r in body.results:
        try:
            eid = uuid.UUID(r.id)
        except ValueError:
            continue
        e = (
            await db.execute(
                select(VendingRecipeEdit).where(
                    VendingRecipeEdit.id == eid, VendingRecipeEdit.machine_id == m.id
                )
            )
        ).scalar_one_or_none()
        if e is None:
            continue
        if r.ok:
            e.status, e.error, e.applied_at = "applied", None, now
        else:
            e.status, e.error = "failed", (r.error or "ismeretlen hiba")[:512]
    m.last_seen_at = now
    await db.commit()
    return {"ok": True}


@router.post(
    "/machines/{machine_id}/promos/restore-factory",
    dependencies=[Depends(require_module("saferpay"))],
)
async def restore_factory_promos(
    machine_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("invoicing")),
):
    """Gyári reklámképek vissza: kizárólagos mód ki (az ügynök visszateszi a
    mentett gyári fájlokat), a mi képeink törlésre jelölve / törölve."""
    m = await _machine_or_404(db, machine_id)
    m.promo_exclusive_making = False
    m.promo_exclusive_screensaver = False
    rows = (
        await db.execute(select(VendingPromo).where(VendingPromo.machine_id == m.id))
    ).scalars().all()
    for p in rows:
        if p.delivered_at is None:
            await db.delete(p)
        else:
            p.status, p.error = "remove_pending", None
    await record_audit(
        db, actor=user, action="vending.promo_restore_factory", entity_type="vending_promo",
        entity_id=m.machine_code, detail={"count": len(rows)}, request=request,
    )
    await db.commit()
    return {"ok": True}


# ─── Ügynök (X-admin config) APK-kiadások — online frissítés ────────────────

AGENT_APK_MAX = 20 * 1024 * 1024


def _release_out(r: AgentRelease) -> dict:
    return {
        "id": str(r.id), "version_code": r.version_code, "version_name": r.version_name,
        "filename": r.filename, "sha256": r.sha256, "size": r.size, "note": r.note,
        "created_at": r.created_at,
    }


async def _latest_release(db: AsyncSession) -> AgentRelease | None:
    return (
        await db.execute(select(AgentRelease).order_by(AgentRelease.version_code.desc()).limit(1))
    ).scalar_one_or_none()


@router.get("/agent/releases", dependencies=[Depends(require_module("saferpay"))])
async def list_releases(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    rows = (
        await db.execute(select(AgentRelease).order_by(AgentRelease.version_code.desc()).limit(20))
    ).scalars().all()
    return [_release_out(r) for r in rows]


class ReleaseBody(BaseModel):
    version_code: int = Field(ge=1, le=1_000_000)
    version_name: str = Field(min_length=1, max_length=32)
    filename: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=512)
    data_b64: str = Field(min_length=16, max_length=28_000_000)


@router.post("/agent/releases", dependencies=[Depends(require_module("saferpay"))])
async def create_release(
    body: ReleaseBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm("invoicing")),
):
    """Új ügynök-APK feltöltése (a gépek a következő napi ellenőrzéskor letöltik,
    a telepítést a gép képernyőjén egy koppintással erősítik meg)."""
    import base64
    import hashlib

    try:
        raw = base64.b64decode(body.data_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail={"code": "vending.bad_apk"})
    # APK = ZIP: "PK\x03\x04" fejléc
    if len(raw) > AGENT_APK_MAX or not raw.startswith(b"PK\x03\x04"):
        raise HTTPException(status_code=422, detail={"code": "vending.bad_apk"})
    dup = (
        await db.execute(select(AgentRelease).where(AgentRelease.version_code == body.version_code))
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=409, detail={"code": "vending.release_exists"})
    r = AgentRelease(
        version_code=body.version_code, version_name=body.version_name.strip(),
        filename=body.filename.strip()[:128], data=raw, sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw), note=(body.note or "").strip() or None,
    )
    db.add(r)
    await record_audit(
        db, actor=user, action="vending.agent_release_uploaded", entity_type="agent_release",
        entity_id=str(body.version_code), detail={"version": r.version_name, "size": r.size},
        request=request,
    )
    await db.commit()
    await db.refresh(r)
    return _release_out(r)


@router.delete("/agent/releases/{release_id}", dependencies=[Depends(require_module("saferpay"))])
async def delete_release(
    release_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    try:
        rid = uuid.UUID(release_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "vending.release_not_found"})
    r = (await db.execute(select(AgentRelease).where(AgentRelease.id == rid))).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail={"code": "vending.release_not_found"})
    await db.delete(r)
    await db.commit()
    return {"ok": True}


class PublishBody(ReleaseBody):
    pass


@public_router.post("/agent/publish")
async def publish_release(
    body: PublishBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """CI (GitHub Actions) teszi közzé az új ügynök-APK-t közzétételi tokennel
    (X-Publish-Token fejléc = WFM_AGENT_PUBLISH_TOKEN). Azonos verziókód → felülírás."""
    import base64
    import hashlib
    import hmac

    token = get_settings().agent_publish_token
    given = request.headers.get("x-publish-token", "")
    if not token or not hmac.compare_digest(given, token):
        raise HTTPException(status_code=403, detail={"code": "vending.bad_publish_token"})
    try:
        raw = base64.b64decode(body.data_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=422, detail={"code": "vending.bad_apk"})
    if len(raw) > AGENT_APK_MAX or not raw.startswith(b"PK"):
        raise HTTPException(status_code=422, detail={"code": "vending.bad_apk"})
    r = (
        await db.execute(select(AgentRelease).where(AgentRelease.version_code == body.version_code))
    ).scalar_one_or_none()
    if r is None:
        r = AgentRelease(version_code=body.version_code)
        db.add(r)
    r.version_name = body.version_name.strip()
    r.filename = body.filename.strip()[:128]
    r.data = raw
    r.sha256 = hashlib.sha256(raw).hexdigest()
    r.size = len(raw)
    r.note = (body.note or "").strip() or None
    await db.commit()
    await db.refresh(r)
    return _release_out(r)


@public_router.post("/agent/latest")
async def agent_latest(body: MachineAuthBody, db: AsyncSession = Depends(get_db)):
    """A GÉP kérdezi: mi a legújabb ügynök-verzió (404, ha nincs feltöltve)."""
    await _machine_by_key(db, body)
    r = await _latest_release(db)
    if r is None:
        raise HTTPException(status_code=404, detail={"code": "vending.no_release"})
    out = _release_out(r)
    out.pop("id", None)
    return out


@public_router.post("/agent/download")
async def agent_download(body: MachineAuthBody, db: AsyncSession = Depends(get_db)):
    await _machine_by_key(db, body)
    r = await _latest_release(db)
    if r is None:
        raise HTTPException(status_code=404, detail={"code": "vending.no_release"})
    return Response(
        content=bytes(r.data), media_type="application/vnd.android.package-archive",
        headers={"X-Sha256": r.sha256, "Content-Disposition": f'attachment; filename="{r.filename}"'},
    )


# ─── Vending hibajelentés ────────────────────────────────────────────────────

ERROR_SOURCES = ("exception", "operation")


def _error_out(e: VendingError) -> dict:
    from app.services.wfm.vending_errors_kb import diagnose, strip_cjk

    return {
        "id": str(e.id), "source": e.source, "message": e.message, "count": e.count,
        "first_seen": e.first_seen, "last_seen": e.last_seen, "resolved_at": e.resolved_at,
        # magyar diagnózis a tudástárból + a nyers sor kínai karakterek nélkül
        "diagnosis": diagnose(e.source, e.message), "technical": strip_cjk(e.message),
    }


@router.post(
    "/machines/{machine_id}/errors/{error_id}/explain",
    dependencies=[Depends(require_module("saferpay"))],
)
async def explain_error(
    machine_id: str,
    error_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    """AI-magyarázat egy naplósorra (ismeretlen mintákhoz) — ai_not_configured, ha nincs AI-kulcs."""
    from app.services.wfm.vending_errors_kb import ai_explain

    m = await _machine_or_404(db, machine_id)
    try:
        eid = uuid.UUID(error_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "vending.error_not_found"})
    e = (await db.execute(select(VendingError).where(VendingError.id == eid, VendingError.machine_id == m.id))).scalar_one_or_none()
    if e is None:
        raise HTTPException(status_code=404, detail={"code": "vending.error_not_found"})
    try:
        text = await ai_explain(db, e.source, e.message, e.count)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "ai.not_configured"})
    except Exception as exc:  # a szolgáltató hibája
        raise HTTPException(status_code=502, detail={"code": "ai.failed", "message": str(exc)[:200]})
    return {"explanation": text}


class ErrorItem(BaseModel):
    source: str = Field(default="exception", max_length=16)
    message: str = Field(min_length=1, max_length=512)
    count: int = Field(default=1, ge=1, le=1_000_000)
    first_seen: str | None = Field(default=None, max_length=32)  # "ÉÉÉÉ:HH:NN óó:pp:mm" (gép helyi idő)
    last_seen: str | None = Field(default=None, max_length=32)


class ErrorsBody(MachineAuthBody):
    errors: list[ErrorItem] = Field(default_factory=list, max_length=200)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite naiv időbélyeget ad vissza — UTC-ként kezeljük az összehasonlításhoz."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _parse_machine_ts(s: str | None, fallback: datetime) -> datetime:
    """A Jetinno-napló időbélyege (2026:10:12 03:02:07) — gép helyi idő, UTC-nek vesszük
    a nagyságrendhez; ha nem értelmezhető, a beérkezés ideje."""
    if not s:
        return fallback
    try:
        return datetime.strptime(s.strip()[:19], "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return fallback


@public_router.post("/errors")
async def report_errors(body: ErrorsBody, db: AsyncSession = Depends(get_db)):
    """A GÉP hibajelentése: üzenet szerint összevont csoportok. Ismert ujjlenyomat →
    darabszám/utolsó előfordulás frissül (lezárt hiba újranyílik); új → Telegram."""
    import hashlib

    from app.services.wfm.telegram import notify_event

    m = await _machine_by_key(db, body)
    now = datetime.now(UTC)
    existing = {
        e.fingerprint: e
        for e in (
            await db.execute(select(VendingError).where(VendingError.machine_id == m.id))
        ).scalars().all()
    }
    new_rows: list[VendingError] = []
    for item in body.errors:
        source = item.source if item.source in ERROR_SOURCES else "exception"
        message = item.message.strip()[:512]
        fp = hashlib.sha256(f"{source}|{message}".encode("utf-8")).hexdigest()
        last = _parse_machine_ts(item.last_seen, now)
        first = _parse_machine_ts(item.first_seen, last)
        e = existing.get(fp)
        if e is None:
            e = VendingError(
                machine_id=m.id, source=source, message=message, fingerprint=fp,
                count=item.count, first_seen=first, last_seen=last,
            )
            db.add(e)
            existing[fp] = e
            new_rows.append(e)
        else:
            if last > _aware(e.last_seen):
                e.last_seen = last
            e.count = max(e.count, item.count) if e.resolved_at is None else item.count
            if e.resolved_at is not None and last > _aware(e.resolved_at):
                e.resolved_at = None  # újra előjött
                new_rows.append(e)
    m.last_seen_at = now
    await db.commit()
    for e in new_rows:
        try:
            await notify_event(db, "vending.error", {
                "gep_nev": m.name, "gep_kod": m.machine_code, "hiba": e.message,
                "forras": e.source, "darab": e.count,
            })
        except Exception:  # az értesítés hibája nem buktathatja a jelentést
            pass
    return {"ok": True, "new": len(new_rows)}


@router.get(
    "/machines/{machine_id}/errors",
    dependencies=[Depends(require_module("saferpay"))],
)
async def machine_errors(
    machine_id: str,
    include_resolved: bool = False,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    m = await _machine_or_404(db, machine_id)
    q = select(VendingError).where(VendingError.machine_id == m.id)
    if not include_resolved:
        q = q.where(VendingError.resolved_at.is_(None))
    rows = (await db.execute(q.order_by(VendingError.last_seen.desc()).limit(200))).scalars().all()
    return [_error_out(e) for e in rows]


@router.post(
    "/machines/{machine_id}/errors/{error_id}/resolve",
    dependencies=[Depends(require_module("saferpay"))],
)
async def resolve_error(
    machine_id: str,
    error_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    m = await _machine_or_404(db, machine_id)
    try:
        eid = uuid.UUID(error_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "vending.error_not_found"})
    e = (
        await db.execute(
            select(VendingError).where(VendingError.id == eid, VendingError.machine_id == m.id)
        )
    ).scalar_one_or_none()
    if e is None:
        raise HTTPException(status_code=404, detail={"code": "vending.error_not_found"})
    e.resolved_at = datetime.now(UTC)
    await db.commit()
    return _error_out(e)


class TelemetryBody(BaseModel):
    machine_code: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=8, max_length=64)
    data: dict = Field(default_factory=dict)


@public_router.post("/telemetry")
async def report_telemetry(body: TelemetryBody, db: AsyncSession = Depends(get_db)):
    """A GÉP jelent ide (heartbeat + mérések) a saját API-kulcsával. A friss
    pillanatkép a gép-sorra kerül, a napló trendhez/hibakereséshez marad."""
    m = (
        await db.execute(
            select(VendingMachine).where(VendingMachine.machine_code == body.machine_code.strip())
        )
    ).scalar_one_or_none()
    if m is None or not m.is_active or not m.api_key or m.api_key != body.key:
        # nem áruljuk el, hogy a gép vagy a kulcs hibás-e
        raise HTTPException(status_code=403, detail={"code": "vending.bad_key"})
    # a payload szigorúan ADAT — méret-korlát a napló-hízás ellen
    data = body.data if isinstance(body.data, dict) else {}
    if len(str(data)) > 8000:
        raise HTTPException(status_code=422, detail={"code": "vending.payload_too_big"})
    m.last_seen_at = datetime.now(UTC)
    m.telemetry = data
    if data:  # üres heartbeat nem kerül a naplóba
        db.add(VendingTelemetry(machine_id=m.id, data=data))
    await db.commit()
    return {"ok": True}


class UploadBody(BaseModel):
    machine_code: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=8, max_length=64)
    kind: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=256)
    text: str = Field(max_length=400_000)


@public_router.post("/upload")
async def upload_file(body: UploadBody, db: AsyncSession = Depends(get_db)):
    """A GÉP tölt fel szöveges fájlt (katalógus = italok + adagok, napló-minta)
    a saját API-kulcsával. Fajtánként a legutóbbi néhány marad meg."""
    m = (
        await db.execute(
            select(VendingMachine).where(VendingMachine.machine_code == body.machine_code.strip())
        )
    ).scalar_one_or_none()
    if m is None or not m.is_active or not m.api_key or m.api_key != body.key:
        raise HTTPException(status_code=403, detail={"code": "vending.bad_key"})
    if body.kind not in FILE_KINDS:
        raise HTTPException(status_code=422, detail={"code": "vending.bad_kind"})
    row = VendingFile(
        machine_id=m.id, kind=body.kind, name=body.name.strip()[:256],
        text=body.text, size=len(body.text.encode("utf-8")),
    )
    db.add(row)
    m.last_seen_at = datetime.now(UTC)
    await db.flush()
    # retenció: fajtánként csak a legutóbbi FILES_KEPT_PER_KIND marad
    old = (
        await db.execute(
            select(VendingFile)
            .where(VendingFile.machine_id == m.id, VendingFile.kind == body.kind)
            .order_by(VendingFile.created_at.desc())
            .offset(FILES_KEPT_OVERRIDE.get(body.kind, FILES_KEPT_PER_KIND))
        )
    ).scalars().all()
    for r in old:
        await db.delete(r)
    await db.commit()
    return {"ok": True, "id": str(row.id)}


async def _order_or_404(db: AsyncSession, order_id: str) -> PaymentOrder:
    try:
        oid = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "saferpay.order_not_found"})
    o = (
        await db.execute(select(PaymentOrder).where(PaymentOrder.id == oid))
    ).scalar_one_or_none()
    if o is None:
        raise HTTPException(status_code=404, detail={"code": "saferpay.order_not_found"})
    return o


async def _finalize(db: AsyncSession, order: PaymentOrder) -> None:
    """Assert + Capture — a notify és a return is ezt hívja (idempotens)."""
    if order.status != "created" or not order.token:
        return
    try:
        res = await saferpay_service.assert_payment(db, order.token)
    except SaferpayError as exc:
        order.error = str(exc)[:512]
        await db.commit()
        return
    if res["status"] in ("AUTHORIZED", "CAPTURED"):
        if res["status"] == "AUTHORIZED" and res["transaction_id"]:
            try:
                await saferpay_service.capture(db, res["transaction_id"])
            except SaferpayError as exc:
                order.error = str(exc)[:512]
                await db.commit()
                return
        order.status = "paid"
        order.paid_at = datetime.now(UTC)
        order.transaction_id = res["transaction_id"]
        order.payment_means = (res["brand"] or "")[:64] or None
        await record_audit(
            db, actor=None, action="saferpay.paid", entity_type="payment_order",
            entity_id=order.order_no,
            detail={"product": order.product_name, "amount": order.amount_huf,
                    "brand": order.payment_means, "machine": order.machine_code},
        )
    await db.commit()


@public_router.get("/{order_id}/notify/ok")
async def notify_ok(order_id: str, db: AsyncSession = Depends(get_db)):
    order = await _order_or_404(db, order_id)
    await _finalize(db, order)
    return {"ok": True}


@public_router.get("/{order_id}/notify/fail")
async def notify_fail(order_id: str, db: AsyncSession = Depends(get_db)):
    order = await _order_or_404(db, order_id)
    if order.status == "created":
        order.status = "failed"
        await record_audit(
            db, actor=None, action="saferpay.failed", entity_type="payment_order",
            entity_id=order.order_no, detail={"product": order.product_name},
        )
        await db.commit()
    return {"ok": True}


@public_router.get("/{order_id}/status")
async def order_status(order_id: str, db: AsyncSession = Depends(get_db)):
    """A gép / teszt-oldal EZT pollozza (a Saferpay-t sosem)."""
    order = await _order_or_404(db, order_id)
    return {
        "status": _effective_status(order),
        "product_name": order.product_name,
        "amount_huf": order.amount_huf,
        "payment_means": order.payment_means,
        "paid_at": order.paid_at,
    }


@public_router.get("/{order_id}/qr.svg")
async def order_qr(order_id: str, db: AsyncSession = Depends(get_db)):
    import segno

    order = await _order_or_404(db, order_id)
    if not order.redirect_url or _effective_status(order) != "created":
        raise HTTPException(status_code=404, detail={"code": "saferpay.order_not_found"})
    import io

    buf = io.BytesIO()
    segno.make(order.redirect_url, error="m").save(buf, kind="svg", scale=6, border=2)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@public_router.get("/{order_id}/return")
async def payment_return(order_id: str, db: AsyncSession = Depends(get_db)):
    """A vevő telefonja ide tér vissza fizetés után — emberbarát oldal.
    A notify a fő csatorna, de itt is véglegesítünk (hátha előbb érünk ide)."""
    order = await _order_or_404(db, order_id)
    await _finalize(db, order)
    status = _effective_status(order)
    if status == "paid":
        title, msg, color = "✅ Sikeres fizetés!", "A gép készíti az italod. Jó kávézást!", "#059669"
    elif status == "created":
        title, msg, color = "⏳ Feldolgozás…", "A fizetés megerősítése folyamatban. Pár másodperc.", "#d97706"
    else:
        title, msg, color = "❌ Sikertelen fizetés", "A fizetés nem jött létre — próbáld újra a gépen.", "#e11d48"
    amount = f"{order.amount_huf:,.0f}".replace(",", " ")
    html = f"""<!doctype html><html lang="hu"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#f8fafc">
<div style="text-align:center;padding:2rem;max-width:22rem">
<h1 style="color:{color};font-size:1.6rem;margin-bottom:.5rem">{title}</h1>
<p style="color:#475569">{msg}</p>
<p style="color:#94a3b8;font-size:.8rem;margin-top:1.5rem">{order.product_name} · {amount} Ft · {order.order_no}</p>
</div></body></html>"""
    return HTMLResponse(html)
