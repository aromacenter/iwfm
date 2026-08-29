"""GLS-csomagfeladás: címke-készítés rendelésből vagy kézzel, utánvéttel;
címke-újranyomtatás és csomagkövetés. A hitelesítő adatok a Beállításokban
(GLS-kártya), a jelszó titkosítva."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import GlsParcel, Partner, ProductOrder, User
from app.services.wfm import couriers, gls_service
from app.services.wfm.license import effective_modules, get_license_row

router = APIRouter()


async def _require_carrier(db: AsyncSession, carrier: str) -> None:
    """403, ha a futár modulja nincs bekapcsolva ezen a példányon."""
    modules = effective_modules(await get_license_row(db))
    if modules is not None and carrier not in modules:
        raise HTTPException(
            status_code=403,
            detail={"code": "license.module_disabled", "module": carrier},
        )


async def _require_any_carrier(db: AsyncSession) -> None:
    modules = effective_modules(await get_license_row(db))
    if modules is not None and not any(c in modules for c in couriers.CARRIERS):
        raise HTTPException(
            status_code=403,
            detail={"code": "license.module_disabled", "module": "gls"},
        )


class ParcelCreateBody(BaseModel):
    carrier: str = Field(default="gls", pattern="^(gls|mpl|foxpost|dpd)$")
    order_id: str | None = None
    partner_id: str | None = None
    recipient_name: str = Field(min_length=2, max_length=256)
    recipient_zip: str = Field(min_length=4, max_length=16)
    recipient_city: str = Field(min_length=2, max_length=128)
    recipient_street: str = Field(min_length=2, max_length=256)
    recipient_house: str | None = Field(default=None, max_length=32)
    recipient_phone: str | None = Field(default=None, max_length=32)
    recipient_email: str | None = Field(default=None, max_length=320)
    content: str | None = Field(default=None, max_length=256)
    count: int = Field(default=1, ge=1, le=20)
    cod_amount: float | None = Field(default=None, ge=0, le=5_000_000)
    cod_reference: str | None = Field(default=None, max_length=64)
    # csomagcsere (XS): kézbesítéskor a futár csere-csomagot hoz el — csak GLS
    exchange: bool = False
    # FoxPost: csomagautomata-kód (üresen házhozszállítás)
    apm_id: str | None = Field(default=None, max_length=32)
    # csomagsúly kg-ban (MPL-nél kötelező jellegű, másutt tájékoztató)
    weight_kg: float | None = Field(default=None, gt=0, le=1000)


class ParcelOut(BaseModel):
    id: str
    carrier: str = "gls"
    parcel_number: str | None
    recipient_name: str
    recipient_city: str
    recipient_zip: str
    content: str | None
    count: int
    cod_amount: float | None
    exchange: bool = False
    # created | handed_over | in_transit | delivered | returned
    status_key: str = "created"
    last_status: str | None
    last_status_at: datetime | None
    # teljes esemény-idővonal (legújabb elöl): [{date, description, depot}]
    history: list = []
    can_delete: bool = False  # amíg a futár nem vette át
    test_mode: bool
    partner_name: str | None = None
    order_no: str | None = None
    created_at: datetime


async def _out(db: AsyncSession, rows: list[GlsParcel]) -> list[ParcelOut]:
    partner_ids = {p.partner_id for p in rows if p.partner_id}
    partners = {
        r.id: r.name for r in (
            await db.execute(select(Partner).where(Partner.id.in_(partner_ids)))
        ).scalars().all()
    } if partner_ids else {}
    order_ids = {p.order_id for p in rows if p.order_id}
    orders = {
        o.id: o.order_no for o in (
            await db.execute(select(ProductOrder).where(ProductOrder.id.in_(order_ids)))
        ).scalars().all()
    } if order_ids else {}
    return [
        ParcelOut(
            id=str(p.id), carrier=p.carrier or "gls", parcel_number=p.parcel_number,
            recipient_name=p.recipient_name, recipient_city=p.recipient_city,
            recipient_zip=p.recipient_zip, content=p.content, count=p.count,
            cod_amount=p.cod_amount, exchange=bool(p.exchange_service),
            status_key=p.status_key or "created",
            last_status=p.last_status,
            last_status_at=p.last_status_at,
            history=p.status_history or [],
            can_delete=(p.status_key or "created") == "created"
            and (
                p.gls_parcel_id is not None
                if (p.carrier or "gls") == "gls"
                else bool(p.carrier_ref or p.parcel_number)
            ),
            test_mode=p.test_mode,
            partner_name=partners.get(p.partner_id) if p.partner_id else None,
            order_no=orders.get(p.order_id) if p.order_id else None,
            created_at=p.created_at,
        )
        for p in rows
    ]


@router.get("", response_model=list[ParcelOut])
async def list_parcels(
    order_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    await _require_any_carrier(db)
    q = select(GlsParcel).order_by(GlsParcel.created_at.desc()).limit(100)
    if order_id:
        try:
            q = q.where(GlsParcel.order_id == uuid.UUID(order_id))
        except ValueError:
            return []
    rows = list((await db.execute(q)).scalars().all())
    return await _out(db, rows)


@router.post("", response_model=ParcelOut)
async def create_parcel(
    body: ParcelCreateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """Címke készítése a választott futárnál; a PDF elmentődik."""
    await _require_carrier(db, body.carrier)
    order = None
    if body.order_id:
        try:
            order = (
                await db.execute(
                    select(ProductOrder).where(ProductOrder.id == uuid.UUID(body.order_id))
                )
            ).scalar_one_or_none()
        except ValueError:
            order = None
    partner_id = None
    if body.partner_id:
        try:
            partner_id = uuid.UUID(body.partner_id)
        except ValueError:
            partner_id = None

    recipient = {
        "name": body.recipient_name.strip(),
        "zip": body.recipient_zip.strip(),
        "city": body.recipient_city.strip(),
        "street": body.recipient_street.strip(),
        "house": (body.recipient_house or "").strip(),
        "phone": (body.recipient_phone or "").strip(),
        "email": (body.recipient_email or "").strip(),
        "apm_id": (body.apm_id or "").strip() or None,
    }
    gls_parcel_id = None
    carrier_ref = None
    try:
        if body.carrier == "gls":
            parcel_number, gls_parcel_id, label_pdf = await gls_service.create_label(
                db,
                recipient=recipient,
                content=body.content,
                count=body.count,
                cod_amount=body.cod_amount,
                cod_reference=body.cod_reference,
                client_reference=order.order_no if order else None,
                exchange=body.exchange,
            )
            cfg_test = (await gls_service.load_gls_config(db))["test_mode"]
        else:
            result = await couriers.create_label(
                db, body.carrier,
                recipient=recipient,
                content=body.content,
                count=body.count,
                cod_amount=body.cod_amount,
                cod_reference=body.cod_reference,
                client_reference=order.order_no if order else None,
                weight_g=int((body.weight_kg or 1.0) * 1000),
            )
            parcel_number = result["tracking_number"]
            carrier_ref = result.get("carrier_ref")
            label_pdf = result.get("label_pdf")
            cfg_test = bool(result.get("test_mode"))
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "gls.not_configured" if body.carrier == "gls"
                else "courier.not_configured",
                "carrier": body.carrier,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502, detail={"code": "gls.error", "message": str(exc)}
        )
    parcel = GlsParcel(
        carrier=body.carrier,
        carrier_ref=carrier_ref,
        order_id=order.id if order else None,
        partner_id=partner_id,
        recipient_name=body.recipient_name.strip(),
        recipient_zip=body.recipient_zip.strip(),
        recipient_city=body.recipient_city.strip(),
        recipient_street=body.recipient_street.strip(),
        recipient_house=(body.recipient_house or "").strip() or None,
        recipient_phone=(body.recipient_phone or "").strip() or None,
        recipient_email=(body.recipient_email or "").strip() or None,
        content=(body.content or "").strip() or None,
        count=body.count,
        cod_amount=body.cod_amount if (body.cod_amount or 0) > 0 else None,
        cod_reference=(body.cod_reference or "").strip() or None,
        exchange_service=body.exchange,
        parcel_number=parcel_number,
        gls_parcel_id=int(gls_parcel_id) if gls_parcel_id else None,
        label_pdf=label_pdf,
        status_key="created",
        test_mode=cfg_test,
        created_by=actor.id,
    )
    db.add(parcel)
    await record_audit(
        db, actor=actor, action="gls.create", entity_type="gls_parcel",
        entity_id=parcel_number,
        detail={"carrier": body.carrier, "to": body.recipient_name,
                "cod": body.cod_amount, "count": body.count,
                "exchange": body.exchange},
        request=request,
    )
    await db.commit()
    await db.refresh(parcel)
    return (await _out(db, [parcel]))[0]


async def _parcel_or_404(db: AsyncSession, parcel_id: str) -> GlsParcel:
    try:
        pid = uuid.UUID(parcel_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "gls.parcel_not_found"})
    p = (
        await db.execute(select(GlsParcel).where(GlsParcel.id == pid))
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "gls.parcel_not_found"})
    return p


@router.get("/{parcel_id}/label")
async def parcel_label(
    parcel_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    """A mentett címke-PDF — nyomtatási ablakhoz / újranyomtatáshoz."""
    p = await _parcel_or_404(db, parcel_id)
    if not p.label_pdf:
        raise HTTPException(status_code=404, detail={"code": "gls.no_label"})
    return Response(
        content=p.label_pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="GLS-{p.parcel_number or "cimke"}.pdf"'
        },
    )


@router.post("/{parcel_id}/refresh-status", response_model=ParcelOut)
async def refresh_parcel_status(
    parcel_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    """A teljes GLS-idővonal lekérése + normalizált státusz frissítése."""
    p = await _parcel_or_404(db, parcel_id)
    await _require_carrier(db, p.carrier or "gls")
    if not p.parcel_number:
        raise HTTPException(status_code=422, detail={"code": "gls.no_parcel_number"})
    try:
        if (p.carrier or "gls") == "gls":
            events = await gls_service.get_statuses(db, p.parcel_number)
        else:
            events = await couriers.get_statuses(
                db, p.carrier, p.parcel_number, p.carrier_ref
            )
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "gls.not_configured" if (p.carrier or "gls") == "gls"
                else "courier.not_configured",
                "carrier": p.carrier,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502, detail={"code": "gls.error", "message": str(exc)}
        )
    p.status_history = events
    p.status_key = couriers.normalize_status(events)
    if events:
        p.last_status = events[0]["description"][:256]
    p.last_status_at = datetime.now(UTC)
    await db.commit()
    return (await _out(db, [p]))[0]


@router.delete("/{parcel_id}")
async def delete_parcel(
    parcel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    """A feladás törlése — CSAK amíg a futár nem vette át (created státusz).
    A címkét a MyGLS-nél is érvénytelenítjük."""
    p = await _parcel_or_404(db, parcel_id)
    await _require_carrier(db, p.carrier or "gls")
    if (p.status_key or "created") != "created":
        raise HTTPException(status_code=422, detail={"code": "gls.already_handed_over"})
    try:
        if (p.carrier or "gls") == "gls":
            if p.gls_parcel_id:
                await gls_service.delete_label(db, p.gls_parcel_id)
        elif p.parcel_number or p.carrier_ref:
            await couriers.delete_parcel(db, p.carrier, p.parcel_number, p.carrier_ref)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "gls.not_configured" if (p.carrier or "gls") == "gls"
                else "courier.not_configured",
                "carrier": p.carrier,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502, detail={"code": "gls.error", "message": str(exc)}
        )
    await record_audit(
        db, actor=actor, action="gls.delete", entity_type="gls_parcel",
        entity_id=p.parcel_number or str(p.id),
        detail={"to": p.recipient_name}, request=request,
    )
    await db.delete(p)
    await db.commit()
    return {"ok": True}
