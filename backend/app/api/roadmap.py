"""Termék-roadmap (115. kör): a Flotta-pult tölti (operator PUT, teljes
csere), minden bejelentkezett felhasználó olvassa a 🗺 Roadmap menüben."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import User

router = APIRouter()


@router.get("")
async def list_roadmap(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.services.wfm import license as license_service

    row = await license_service.get_license_row(db)
    items = list((row.roadmap if row is not None else None) or [])
    items.sort(key=lambda x: int(x.get("sort") or 0))
    return [
        {
            "id": str(i),
            "title": it.get("title") or "",
            "description": it.get("description") or "",
            "status": it.get("status") or "planned",
            "eta": it.get("eta") or "",
        }
        for i, it in enumerate(items)
    ]
