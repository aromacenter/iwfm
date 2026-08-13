"""Irányítószám → város feloldás + útvonal-optimalizálás (magyar).

Adatforrás: GeoNames postal codes (https://download.geonames.org/export/zip/),
CC-BY 4.0 — a data/hu_zip_city.json és data/hu_zip_latlng.json build-időben
generált kivonatok. Futásidőben minden offline megy, külső hívás nincs.
"""

from __future__ import annotations

import json
import math
import uuid
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_perm
from app.db import get_db
from app.models import Partner, User

router = APIRouter()

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "hu_zip_city.json"
_LATLNG_FILE = Path(__file__).resolve().parent.parent / "data" / "hu_zip_latlng.json"


@lru_cache(maxsize=1)
def _zip_map() -> dict[str, str]:
    try:
        return json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - hiányzó/sérült adatfájl
        return {}


@lru_cache(maxsize=1)
def _latlng_map() -> dict[str, list[float]]:
    """{"1051": [lat, lng], ..., "city:budapest": [lat, lng]} — offline kivonat."""
    try:
        return json.loads(_LATLNG_FILE.read_text(encoding="utf-8"))
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


# ─── Körút-optimalizálás (üzletkötői útvonal) ───────────────────────────────


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Légvonalbeli távolság km-ben."""
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _shortest_open_path(coords: list[tuple[float, float]]) -> list[int]:
    """A legrövidebb nyílt útvonal (Hamilton-út, szabad végpontokkal) —
    EGZAKT megoldás Held–Karp dinamikus programozással. n ≤ 10 megállóig
    pillanatok alatt fut (2^n·n² lépés)."""
    n = len(coords)
    if n <= 2:
        return list(range(n))
    dist = [[_haversine(coords[i], coords[j]) for j in range(n)] for i in range(n)]
    inf = float("inf")
    size = 1 << n
    dp = [[inf] * n for _ in range(size)]
    parent = [[-1] * n for _ in range(size)]
    for i in range(n):
        dp[1 << i][i] = 0.0
    for mask in range(size):
        row = dp[mask]
        for last in range(n):
            base = row[last]
            if base == inf or not mask & (1 << last):
                continue
            for nxt in range(n):
                if mask & (1 << nxt):
                    continue
                nm = mask | (1 << nxt)
                nd = base + dist[last][nxt]
                if nd < dp[nm][nxt]:
                    dp[nm][nxt] = nd
                    parent[nm][nxt] = last
    full = size - 1
    end = min(range(n), key=lambda i: dp[full][i])
    order: list[int] = []
    mask, cur = full, end
    while cur != -1:
        order.append(cur)
        prev = parent[mask][cur]
        mask ^= 1 << cur
        cur = prev
    return order[::-1]


def _path_km(coords: list[tuple[float, float]], order: list[int]) -> float:
    return round(
        sum(_haversine(coords[order[i]], coords[order[i + 1]]) for i in range(len(order) - 1)),
        1,
    )


def _partner_coords(p: Partner) -> tuple[float, float] | None:
    """Koordináta a partner irányítószámából (mezőből vagy a cím elejéről),
    ennek híján a település-középpontból."""
    import re

    table = _latlng_map()
    zip_in_address = re.match(r"\s*(\d{4})\b", p.address or "")
    for key in (
        (p.address_zip or "").strip(),
        zip_in_address.group(1) if zip_in_address else "",
        f"city:{(p.address_city or '').strip().lower()}" if p.address_city else "",
    ):
        if key and key in table:
            lat, lng = table[key]
            return (lat, lng)
    return None


class RouteOptimizeBody(BaseModel):
    partner_ids: list[str] = Field(min_length=2, max_length=10)


class RouteStopOut(BaseModel):
    partner_id: str
    name: str
    address: str | None
    located: bool  # volt-e koordináta (ha nem, a sor végére kerül)


class RouteOptimizeOut(BaseModel):
    stops: list[RouteStopOut]
    total_km: float | None  # az optimalizált útvonal hossza (légvonal-összeg)
    original_km: float | None  # a beküldött sorrend hossza (összehasonlításhoz)


@router.post("/optimize-route", response_model=RouteOptimizeOut)
async def optimize_route(
    body: RouteOptimizeBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    """Az üzletkötői körút megállóinak sorbarendezése a legrövidebb útvonalra.

    A koordináták a beépített GeoNames-kivonatból jönnek (irányítószám, ennek
    híján település-középpont). A koordináta nélküli partnerek az optimalizált
    sor végére kerülnek, az eredeti sorrendjükben."""
    ids: list[uuid.UUID] = []
    for raw in body.partner_ids:
        try:
            ids.append(uuid.UUID(raw))
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    rows = (
        await db.execute(select(Partner).where(Partner.id.in_(ids)))
    ).scalars().all()
    by_id = {p.id: p for p in rows}
    ordered_input = [by_id[i] for i in ids if i in by_id]
    if len(ordered_input) < 2:
        raise HTTPException(status_code=422, detail={"code": "route.too_few_stops"})

    located: list[tuple[Partner, tuple[float, float]]] = []
    unlocated: list[Partner] = []
    for p in ordered_input:
        coords = _partner_coords(p)
        (located.append((p, coords)) if coords else unlocated.append(p))

    def _address(p: Partner) -> str | None:
        if p.address:
            return p.address
        parts = [x for x in (p.address_zip, p.address_city, p.address_street, p.address_number) if x]
        return " ".join(parts) or None

    total_km = original_km = None
    if len(located) >= 2:
        coords = [c for _, c in located]
        order = _shortest_open_path(coords)
        total_km = _path_km(coords, order)
        original_km = _path_km(coords, list(range(len(coords))))
        located = [located[i] for i in order]

    stops = [
        RouteStopOut(partner_id=str(p.id), name=p.name, address=_address(p), located=True)
        for p, _c in located
    ] + [
        RouteStopOut(partner_id=str(p.id), name=p.name, address=_address(p), located=False)
        for p in unlocated
    ]
    return RouteOptimizeOut(stops=stops, total_km=total_km, original_km=original_km)
