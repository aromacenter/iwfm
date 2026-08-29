"""Üzemeltetői (Flotta-pult) végpontok: a példány licencének táv-kezelése.

Csak a WFM_OPERATOR_TOKEN birtokában hívhatók (X-Operator-Token fejléc) —
a tokent a Flotta-pult tárolja titkosítva; beállítatlan tokennél a végpontok
teljesen zárva vannak. Lejárt licencnél is működnek (a hosszabbításhoz épp
ez kell), ezért a lejárat-középréteg kivétel-listáján szerepelnek.
"""

from __future__ import annotations

import secrets as _secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
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


@router.put("/license", dependencies=[Depends(require_operator)])
async def operator_set_license(
    body: LicenseBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Licenc-sáv / érvényesség állítása a Flotta-pultból (actor nélkül,
    de audit-naplóval)."""
    return await apply_license_update(db, body, actor=None, request=request)
