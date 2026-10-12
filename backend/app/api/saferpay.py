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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, record_audit, require_perm
from app.core.config import get_settings
from app.db import get_db
from app.models import PaymentOrder, PaymentProduct, User, VendingMachine, VendingTelemetry
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


def _machine_out(m: VendingMachine, paid_today: int = 0, revenue_today: float = 0.0) -> MachineOut:
    return MachineOut(
        id=str(m.id), machine_code=m.machine_code, name=m.name, note=m.note,
        api_key=m.api_key, is_active=m.is_active, last_seen_at=m.last_seen_at,
        telemetry=m.telemetry, paid_today=paid_today, revenue_today=revenue_today,
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
    return [
        _machine_out(m, *(stats.get(m.machine_code, (0, 0.0))))
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
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    m = await _machine_or_404(db, machine_id)
    rows = (
        await db.execute(
            select(VendingTelemetry)
            .where(VendingTelemetry.machine_id == m.id)
            .order_by(VendingTelemetry.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [{"created_at": r.created_at, "data": r.data} for r in rows]


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
