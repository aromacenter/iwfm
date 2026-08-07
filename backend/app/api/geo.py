"""Irányítószám → város feloldás (magyar).

Adatforrás: GeoNames postal codes (https://download.geonames.org/export/zip/),
CC-BY 4.0 — a data/hu_zip_city.json build-időben generált kivonat (3046 kód).
A partner-űrlap tölti ki vele a város mezőt automatikusan.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.models import User

router = APIRouter()

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "hu_zip_city.json"


@lru_cache(maxsize=1)
def _zip_map() -> dict[str, str]:
    try:
        return json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - hiányzó/sérült adatfájl
        return {}


@router.get("/zip/{zip_code}")
async def resolve_zip(
    zip_code: str,
    _: User = Depends(get_current_user),
):
    city = _zip_map().get(zip_code.strip())
    if city is None:
        raise HTTPException(status_code=404, detail={"code": "geo.zip_not_found"})
    return {"zip": zip_code.strip(), "city": city}
