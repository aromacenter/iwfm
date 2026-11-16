"""Régi rendszerből átemelt elszámolások (156. kör): visszadátumozott
elszámolás-sorok rögzítése készlet-levonás, kassza-mozgás és számlázás NÉLKÜL.

Csak admin futtathatja. Minden tétel a `Régi rendszer #<azonosító>` jelölést
kapja a megjegyzésben — ismételt futásnál a már átemelt tétel kimarad, így a
hívás idempotens. A gép számlálója csak akkor frissül, ha az átemelt állás
nagyobb a jelenleginél."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import (
    Asset,
    AssetMovement,
    Partner,
    Product,
    Settlement,
    SettlementLine,
    SettlementMachine,
    User,
)
from app.services.wfm.license import require_module

router = APIRouter()
MARK = "Régi rendszer #"


def _norm(t: str | None) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = re.sub(r"\b(kft|bt|zrt|nyrt|kkt|ev|e\.v\.|rt)\b\.?", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


class BackfillMachine(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    new_counter: int = Field(default=0, ge=0)
    portions: float = Field(default=0, ge=0)
    price_per_portion: float = Field(default=0, ge=0)
    amount_net: float = Field(default=0, ge=0)


class BackfillLine(BaseModel):
    product_name: str = Field(min_length=1, max_length=256)
    qty: float = Field(default=0, ge=0)
    price: float = Field(default=0, ge=0)  # nettó egységár


class BackfillItem(BaseModel):
    legacy_id: str = Field(min_length=1, max_length=32)
    created_at: datetime
    partner_name: str = Field(min_length=1, max_length=256)
    company_name: str | None = Field(default=None, max_length=256)
    tax_number: str | None = Field(default=None, max_length=32)
    payment_method: str = Field(default="cash", pattern="^(cash|card|transfer)$")
    total_gross: float = Field(ge=0)
    total_net: float | None = Field(default=None, ge=0)
    settled_by: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, max_length=512)
    machines: list[BackfillMachine] = Field(default_factory=list, max_length=50)
    lines: list[BackfillLine] = Field(default_factory=list, max_length=100)


class BackfillBody(BaseModel):
    items: list[BackfillItem] = Field(min_length=1, max_length=500)
    dry_run: bool = False


async def _find_partner(db: AsyncSession, partners: list[Partner], it: BackfillItem) -> Partner | None:
    if it.tax_number:
        digits = re.sub(r"\D", "", it.tax_number)[:8]
        if len(digits) == 8:
            for p in partners:
                if re.sub(r"\D", "", p.tax_number or "")[:8] == digits:
                    return p
    for key in (it.partner_name, it.company_name):
        n = _norm(key)
        if not n:
            continue
        for p in partners:
            if _norm(p.name) == n:
                return p
        for p in partners:
            if _norm(p.company_name) == n:
                return p
    return None


@router.post("", dependencies=[Depends(require_module("settlements"))])
async def backfill_settlements(
    body: BackfillBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("settlements")),
):
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "auth.admin_only"})
    partners = list((await db.execute(select(Partner))).scalars())
    products = list((await db.execute(select(Product))).scalars())
    prod_by_norm = {_norm(p.name): p for p in products}
    users = list((await db.execute(select(User))).scalars())
    user_by_name = {_norm(u.display_name): u for u in users}
    existing_notes = set(
        (await db.execute(select(Settlement.note).where(Settlement.note.like(MARK + "%")))).scalars()
    )
    done_ids = {n[len(MARK):].split(" ")[0] for n in existing_notes if n}

    results = []
    created = skipped = 0
    for it in body.items:
        if it.legacy_id in done_ids:
            skipped += 1
            results.append({"legacy_id": it.legacy_id, "status": "skipped_exists"})
            continue
        partner = await _find_partner(db, partners, it)
        if partner is None:
            results.append({"legacy_id": it.legacy_id, "status": "partner_not_found", "partner_name": it.partner_name})
            continue
        if body.dry_run:
            results.append({"legacy_id": it.legacy_id, "status": "would_create", "partner": partner.name})
            continue
        total_net = it.total_net if it.total_net is not None else round(it.total_gross / 1.27, 2)
        by_user = user_by_name.get(_norm(it.settled_by)) if it.settled_by else None
        note = f"{MARK}{it.legacy_id} — átemelve a régi rendszerből" + (f" — {it.note}" if it.note else "")
        s = Settlement(
            partner_id=partner.id,
            settled_by_user_id=(by_user or actor).id,
            settled_by_name=it.settled_by or "Régi rendszer",
            invoicing_company=partner.invoicing_company,
            payment_method=it.payment_method,
            no_vat=False,
            total_net=float(total_net),
            total_gross=float(it.total_gross),
            invoiced=False,
            payment_status="paid",
            paid_at=it.created_at,
            paid_amount=float(it.total_gross),
            note=note,
            created_at=it.created_at,
        )
        db.add(s)
        await db.flush()
        missing_assets = []
        for m in it.machines:
            asset = (await db.execute(select(Asset).where(Asset.barcode == m.barcode))).scalar_one_or_none()
            if asset is None:
                missing_assets.append(m.barcode)
            db.add(SettlementMachine(
                settlement_id=s.id, asset_id=asset.id if asset else None, barcode=m.barcode,
                asset_name=(asset.name if asset else "ismeretlen gép (régi rendszer)"),
                product_id=(asset.default_product_id if asset else None),
                prev_counter=max(int(m.new_counter - m.portions), 0), new_counter=int(m.new_counter),
                portions_billed=float(m.portions), price_per_portion=float(m.price_per_portion),
                amount_net=float(m.amount_net), created_at=it.created_at,
            ))
            if asset is not None and m.new_counter > int(asset.counter or 0):
                db.add(AssetMovement(
                    asset_id=asset.id, action="counter", partner_id=partner.id, actor_user_id=actor.id,
                    detail=f"Számláló {asset.counter} → {m.new_counter} (régi rendszer #{it.legacy_id}, {it.created_at:%Y-%m-%d})"[:512],
                ))
                asset.counter = int(m.new_counter)
        for ln in it.lines:
            product = prod_by_norm.get(_norm(ln.product_name))
            db.add(SettlementLine(
                settlement_id=s.id, product_id=product.id if product else None, product_name=ln.product_name[:256],
                previous_qty=0.0, physical_qty=0.0, consumed_qty=0.0, portions=float(ln.qty),
                price_per_portion=float(ln.price), vat_percent=27, amount_net=round(float(ln.qty) * float(ln.price), 2),
            ))
        created += 1
        results.append({"legacy_id": it.legacy_id, "status": "created", "id": str(s.id), "partner": partner.name,
                        "missing_assets": missing_assets})
    if not body.dry_run:
        await record_audit(
            db, actor=actor, action="settlement.backfill", entity_type="settlement",
            detail={"created": created, "skipped": skipped, "items": len(body.items)}, request=request,
        )
        await db.commit()
    return {"created": created, "skipped": skipped, "dry_run": body.dry_run, "results": results}

