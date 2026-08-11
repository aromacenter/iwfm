"""Csak-admin karbantartó eszközök: teljes törlés entitásonként (veszélyzóna).

A gépek / termékek / partnerek MINDEN sorát törli egy hívással, a függő
rekordok explicit takarításával (SQLite-on nincs FK-cascade garancia).
FIGYELEM: a partnerek törlése az elszámolás-előzményeket is törli (a
Settlement partner-FK-ja RESTRICT) — a kliens ezt hangsúlyosan jelzi.
A hívást a kötelező confirm="TORLES" mező is védi, minden művelet auditált.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, func, select, update as sa_update
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
