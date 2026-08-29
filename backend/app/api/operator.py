"""Üzemeltetői (Flotta-pult) végpontok: a példány licencének táv-kezelése.

Csak a WFM_OPERATOR_TOKEN birtokában hívhatók (X-Operator-Token fejléc) —
a tokent a Flotta-pult tárolja titkosítva; beállítatlan tokennél a végpontok
teljesen zárva vannak. Lejárt licencnél is működnek (a hosszabbításhoz épp
ez kell), ezért a lejárat-középréteg kivétel-listáján szerepelnek.
"""

from __future__ import annotations

import secrets as _secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.settings import LicenseBody, _license_status_payload, apply_license_update
from app.core.config import get_settings
from app.db import get_db

router = APIRouter()


async def require_operator(
    x_operator_token: str | None = Header(default=None),
) -> None:
    token = get_settings().operator_token
    if (
        not token
        or not x_operator_token
        or not _secrets.compare_digest(token, x_operator_token)
    ):
        raise HTTPException(status_code=403, detail={"code": "operator.forbidden"})


@router.get("/status", dependencies=[Depends(require_operator)])
async def operator_status(db: AsyncSession = Depends(get_db)):
    """Példány-állapot a Flotta-pultnak: licenc + kihasználtság."""
    return {"app": "iwfm", **(await _license_status_payload(db))}


class PlanCatalogBody(BaseModel):
    # [{code, name, max_users, max_employees, price_monthly, price_yearly}]
    plans: list[dict] = Field(max_length=50)


@router.put("/plan-catalog", dependencies=[Depends(require_operator)])
async def operator_set_plan_catalog(
    body: PlanCatalogBody,
    db: AsyncSession = Depends(get_db),
):
    """A Flotta-pult csomag-katalógusának lenyomása — az ügyfél-oldali
    "Elérhető csomagok" kártyák ebből épülnek (név + limit + ár)."""
    from app.models import LicenseSettings
    from app.services.wfm import license as license_service

    row = await license_service.get_license_row(db)
    if row is None:
        row = LicenseSettings(id=1)
        db.add(row)
    cleaned = []
    for p in body.plans[:50]:
        code = str(p.get("code") or "").strip().lower()[:16]
        if not code:
            continue
        cleaned.append({
            "code": code,
            "name": str(p.get("name") or code.upper())[:64],
            "max_users": p.get("max_users"),
            "max_employees": p.get("max_employees"),
            "price_monthly": p.get("price_monthly"),
            "price_yearly": p.get("price_yearly"),
        })
    row.plan_catalog = cleaned
    await db.commit()
    license_service.invalidate_cache()
    return {"ok": True, "count": len(cleaned)}


@router.put("/license", dependencies=[Depends(require_operator)])
async def operator_set_license(
    body: LicenseBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Licenc-sáv / érvényesség állítása a Flotta-pultból (actor nélkül,
    de audit-naplóval)."""
    return await apply_license_update(db, body, actor=None, request=request)
