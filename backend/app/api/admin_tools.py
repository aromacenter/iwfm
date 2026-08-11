"""Csak-admin karbantartó eszközök: teljes törlés entitásonként (veszélyzóna).

A gépek / termékek / partnerek MINDEN sorát törli egy hívással, a függő
rekordok explicit takarításával (SQLite-on nincs FK-cascade garancia).
FIGYELEM: a partnerek törlése az elszámolás-előzményeket is törli (a
Settlement partner-FK-ja RESTRICT) — a kliens ezt hangsúlyosan jelzi.
A hívást a kötelező confirm="TORLES" mező is védi, minden művelet auditált.
"""

from __future__ import annotations

import base64
import gzip
import io
import json
from datetime import date, datetime, time as dt_time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, func, select, text as sa_text, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_role
from app.db import get_db
from app.models import (
    Asset,
    AssetMovement,
    Partner,
    PartnerPrice,
    PartnerStock,
    Product,
    ProductOrder,
    ServiceTicket,
    Settlement,
    SettlementLine,
    StockMovement,
    User,
)

router = APIRouter()

WIPE_ENTITIES = ("assets", "products", "partners")
CONFIRM_WORD = "TORLES"


# ─── Teljes adatbázis-mentés (letölthető) ───────────────────────────────────


def _jsonable(value):
    if isinstance(value, (datetime, date, dt_time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return value


async def build_backup_gzip(db: AsyncSession) -> bytes:
    """Teljes logikai mentés gzip-elt JSON-ként (a heti auto-mentés is ezt
    használja)."""
    from app.models import Base

    dump: dict[str, list[dict]] = {}
    for table in Base.metadata.sorted_tables:
        rows = (await db.execute(sa_text(f'SELECT * FROM "{table.name}"'))).mappings().all()
        dump[table.name] = [
            {k: _jsonable(v) for k, v in row.items()} for row in rows
        ]
    payload = json.dumps(
        {"format": "iwfm-backup-v1", "created_at": datetime.now().isoformat(), "tables": dump},
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(payload)
    return buf.getvalue()


@router.get("/backup")
async def download_backup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    """Teljes logikai mentés: minden tábla minden sora, gzip-elt JSON-ban.
    A bináris mezők (aláírások, csatolmányok, titkosított kulcsok) base64-ben.
    Visszatöltés: soronkénti INSERT az azonos sémájú üres adatbázisba."""
    content = await build_backup_gzip(db)
    await record_audit(
        db, actor=actor, action="admin.backup", entity_type="backup",
        detail={"bytes": len(content)}, request=request,
    )
    await db.commit()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Response(
        content=content,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="iwfm-mentes-{stamp}.json.gz"'},
    )


@router.get("/wipe/counts")
async def wipe_counts(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    async def count(model) -> int:
        return (await db.execute(select(func.count()).select_from(model))).scalar_one()

    return {
        "assets": await count(Asset),
        "products": await count(Product),
        "partners": await count(Partner),
        "settlements": await count(Settlement),
    }


# ─── Billingó vevőtörzs-szinkron ────────────────────────────────────────────


class BillingoSyncBody(BaseModel):
    dry_run: bool = True


@router.post("/billingo-sync")
async def billingo_sync(
    body: BillingoSyncBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    """Cégadatok frissítése a Billingó vevőtörzséből, cégenként a saját
    fiókkal (xp partnerek ← X-Presso fiók, pc ← Premium Caffe). Párosítás
    adószám-törzsszámra (első 8 jegy). Csak az ÜRES mezőket tölti:
    company_name, billing cím, contact_email. dry_run=True: csak jelentés."""
    import re as _re

    import httpx

    from app.services.wfm.billingo_service import _account, get_or_create_settings

    settings = await get_or_create_settings(db)
    partners = (await db.execute(select(Partner))).scalars().all()
    report: dict[str, dict] = {}
    total_updated = 0

    async with httpx.AsyncClient(timeout=30) as client:
        for company in ("xp", "pc"):
            api_key, _block, _test = _account(settings, company)
            if not api_key:
                report[company] = {"skipped": "nincs API-kulcs"}
                continue
            # Billingó partnerlista lapozva (max 40 × 100 = 4000 vevő)
            by_tax: dict[str, dict] = {}
            fetched = 0
            for page in range(1, 41):
                res = await client.get(
                    f"https://api.billingo.hu/v3/partners?page={page}&per_page=100",
                    headers={"X-API-KEY": api_key},
                )
                res.raise_for_status()
                data = res.json().get("data", [])
                if not data:
                    break
                for item in data:
                    fetched += 1
                    digits = _re.sub(r"\D", "", str(item.get("taxcode") or ""))
                    if len(digits) >= 8:
                        by_tax.setdefault(digits[:8], item)
                if len(data) < 100:
                    break

            matched = updated = 0
            samples: list[str] = []
            for p in partners:
                if p.invoicing_company != company:
                    continue
                digits = _re.sub(r"\D", "", p.tax_number or "")
                item = by_tax.get(digits[:8]) if len(digits) >= 8 else None
                if item is None:
                    continue
                matched += 1
                changes: list[str] = []
                b_name = str(item.get("name") or "").strip()
                if b_name and not p.company_name:
                    changes.append(f"cégnév → {b_name}")
                    if not body.dry_run:
                        p.company_name = b_name[:256]
                addr = item.get("address") or {}
                if addr.get("post_code") and not p.billing_zip:
                    changes.append("számlázási cím")
                    if not body.dry_run:
                        p.billing_zip = str(addr.get("post_code"))[:16]
                        p.billing_city = str(addr.get("city") or "")[:128] or None
                        p.billing_street = str(addr.get("address") or "")[:256] or None
                        p.billing_address = ", ".join(
                            x for x in (
                                f"{addr.get('post_code')} {addr.get('city') or ''}".strip(),
                                str(addr.get("address") or ""),
                            ) if x
                        )[:512] or None
                emails = item.get("emails") or []
                if emails and not p.contact_email:
                    changes.append(f"e-mail → {emails[0]}")
                    if not body.dry_run:
                        p.contact_email = str(emails[0])[:320]
                if changes:
                    updated += 1
                    if len(samples) < 8:
                        samples.append(f"{p.name}: {', '.join(changes)}")
            total_updated += updated
            report[company] = {
                "billingo_partners": fetched, "matched": matched,
                "would_update" if body.dry_run else "updated": updated,
                "samples": samples,
            }

    if not body.dry_run and total_updated:
        await record_audit(
            db, actor=actor, action="partner.billingo_sync", entity_type="partner",
            detail={"updated": total_updated}, request=request,
        )
        await db.commit()
    return {"dry_run": body.dry_run, "report": report}


class WipeBody(BaseModel):
    entity: str
    confirm: str


@router.post("/wipe")
async def wipe_entity(
    body: WipeBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    if body.entity not in WIPE_ENTITIES:
        raise HTTPException(status_code=422, detail={"code": "admin.bad_entity"})
    if body.confirm.strip().upper() != CONFIRM_WORD:
        raise HTTPException(status_code=422, detail={"code": "admin.bad_confirm"})

    deleted = 0
    if body.entity == "assets":
        deleted = (
            await db.execute(select(func.count()).select_from(Asset))
        ).scalar_one()
        await db.execute(sa_delete(AssetMovement))
        await db.execute(sa_update(ServiceTicket).values(asset_id=None))
        await db.execute(sa_update(ProductOrder).values(asset_id=None))
        await db.execute(sa_delete(Asset))

    elif body.entity == "products":
        deleted = (
            await db.execute(select(func.count()).select_from(Product))
        ).scalar_one()
        await db.execute(sa_update(SettlementLine).values(product_id=None))
        await db.execute(sa_delete(PartnerPrice))
        await db.execute(sa_delete(StockMovement))
        await db.execute(sa_delete(PartnerStock))
        await db.execute(sa_delete(Product))

    elif body.entity == "partners":
        deleted = (
            await db.execute(select(func.count()).select_from(Partner))
        ).scalar_one()
        # elszámolások + készletek a partnerekkel együtt törlődnek
        await db.execute(sa_delete(SettlementLine))
        await db.execute(sa_delete(StockMovement))
        await db.execute(sa_delete(Settlement))
        await db.execute(sa_delete(PartnerPrice))
        await db.execute(sa_delete(PartnerStock))
        await db.execute(sa_update(AssetMovement).values(partner_id=None))
        await db.execute(
            sa_update(Asset)
            .where(Asset.status == "deployed")
            .values(status="in_stock", partner_id=None, deployed_at=None)
        )
        await db.execute(sa_update(Asset).values(partner_id=None))
        await db.execute(sa_update(ServiceTicket).values(partner_id=None))
        await db.execute(sa_update(ProductOrder).values(partner_id=None))
        await db.execute(sa_delete(Partner))

    await record_audit(
        db, actor=actor, action="admin.wipe", entity_type=body.entity,
        detail={"deleted": deleted}, request=request,
    )
    await db.commit()
    return {"entity": body.entity, "deleted": deleted}
