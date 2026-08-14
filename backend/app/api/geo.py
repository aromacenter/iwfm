"""Irányítószám → város feloldás + útvonal-optimalizálás (magyar).

Adatforrás: GeoNames postal codes (https://download.geonames.org/export/zip/),
CC-BY 4.0 — a data/hu_zip_city.json és data/hu_zip_latlng.json build-időben
generált kivonatok. Futásidőben minden offline megy, külső hívás nincs.
"""

from __future__ import annotations

import json
import math
import os
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


def _haversine_matrix(coords: list[tuple[float, float]]) -> list[list[float]]:
    n = len(coords)
    return [[_haversine(coords[i], coords[j]) for j in range(n)] for i in range(n)]


async def _osrm_matrix(
    coords: list[tuple[float, float]],
) -> tuple[list[list[float]], list[list[float]]] | None:
    """Valós közúti (távolság km, menetidő perc) mátrix az OSRM `table`
    szolgáltatásból. Best-effort: hiba/timeout esetén None — ilyenkor a hívó
    a légvonal-mátrixra esik vissza. Env: WFM_OSRM_URL ('' = kikapcsolva)."""
    import httpx

    base = os.environ.get("WFM_OSRM_URL", "https://router.project-osrm.org")
    if not base:
        return None
    pts = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in coords)
    try:
        async with httpx.AsyncClient(timeout=8.0) as cx:
            res = await cx.get(
                f"{base}/table/v1/driving/{pts}",
                params={"annotations": "distance,duration"},
            )
        res.raise_for_status()
        data = res.json()
        if data.get("code") != "Ok":
            return None
        dist = data["distances"]  # méter
        dur = data["durations"]  # másodperc
        if any(cell is None for row in dist for cell in row):
            return None
        km = [[cell / 1000.0 for cell in row] for row in dist]
        minutes = [[cell / 60.0 for cell in row] for row in dur]
        return km, minutes
    except Exception:  # pragma: no cover - hálózati hiba: fallback légvonalra
        return None


def _shortest_open_path(dist: list[list[float]]) -> list[int]:
    """A legrövidebb nyílt útvonal (Hamilton-út, szabad végpontokkal) a
    megadott távolságmátrixon — EGZAKT megoldás Held–Karp dinamikus
    programozással. n ≤ 10 megállóig pillanatok alatt fut (2^n·n² lépés)."""
    n = len(dist)
    if n <= 2:
        return list(range(n))
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


def _nearest_neighbor_2opt(dist: list[list[float]]) -> list[int]:
    """Heurisztikus sorrend n > 10 megállóra: legjobb legközelebbi-szomszéd
    kezdés minden kiindulópontból + 2-opt javítás."""
    n = len(dist)

    def path_len(order: list[int]) -> float:
        return sum(dist[order[i]][order[i + 1]] for i in range(len(order) - 1))

    best: list[int] | None = None
    best_len = float("inf")
    for start in range(n):
        order = [start]
        left = set(range(n)) - {start}
        while left:
            nxt = min(left, key=lambda j: dist[order[-1]][j])
            order.append(nxt)
            left.remove(nxt)
        # 2-opt: szakaszcserék, amíg javul
        improved = True
        while improved:
            improved = False
            for i in range(1, n - 1):
                for j in range(i + 1, n):
                    cand = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                    if path_len(cand) < path_len(order) - 1e-9:
                        order = cand
                        improved = True
        length = path_len(order)
        if length < best_len:
            best, best_len = order, length
    return best or list(range(n))


def _order_stops(dist: list[list[float]]) -> list[int]:
    """Sorrend a mátrixon: ≤10 megállóig egzakt, fölötte heurisztika."""
    return _shortest_open_path(dist) if len(dist) <= 10 else _nearest_neighbor_2opt(dist)


def _path_km(dist: list[list[float]], order: list[int]) -> float:
    return round(sum(dist[order[i]][order[i + 1]] for i in range(len(order) - 1)), 1)


def _partner_coords(p: Partner) -> tuple[float, float] | None:
    """Koordináta: geokódolt lat/lng, ennek híján irányítószám (mezőből vagy
    a cím elejéről), végül a település-középpont."""
    import re

    if p.lat is not None and p.lng is not None:
        return (p.lat, p.lng)
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
    total_km: float | None  # az optimalizált útvonal hossza
    original_km: float | None  # a beküldött sorrend hossza (összehasonlításhoz)
    total_min: float | None = None  # becsült menetidő (csak közúti mátrixnál)
    used_roads: bool = False  # közúti (OSRM) távolság volt-e, vagy légvonal


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
    ordered_input = await _load_route_partners(db, body.partner_ids)

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

    total_km = original_km = total_min = None
    used_roads = False
    if len(located) >= 2:
        coords = [c for _, c in located]
        road = await _osrm_matrix(coords)
        if road is not None:
            dist, dur = road
            used_roads = True
        else:
            dist, dur = _haversine_matrix(coords), None
        order = _order_stops(dist)
        total_km = _path_km(dist, order)
        original_km = _path_km(dist, list(range(len(dist))))
        if dur is not None:
            total_min = _path_km(dur, order)
        located = [located[i] for i in order]

    stops = [
        RouteStopOut(partner_id=str(p.id), name=p.name, address=_address(p), located=True)
        for p, _c in located
    ] + [
        RouteStopOut(partner_id=str(p.id), name=p.name, address=_address(p), located=False)
        for p in unlocated
    ]
    return RouteOptimizeOut(
        stops=stops, total_km=total_km, original_km=original_km,
        total_min=total_min, used_roads=used_roads,
    )


# ─── Heti körút-tervező ─────────────────────────────────────────────────────


class PlanWeekBody(BaseModel):
    partner_ids: list[str] = Field(min_length=2, max_length=60)
    days: int = Field(default=5, ge=1, le=6)


class PlanDayOut(BaseModel):
    stops: list[RouteStopOut]
    total_km: float | None


class PlanWeekOut(BaseModel):
    days: list[PlanDayOut]
    total_km: float | None
    used_roads: bool = False


async def _load_route_partners(db: AsyncSession, raw_ids: list[str]) -> list[Partner]:
    ids: list[uuid.UUID] = []
    for raw in raw_ids:
        try:
            ids.append(uuid.UUID(raw))
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    rows = (
        await db.execute(select(Partner).where(Partner.id.in_(ids)))
    ).scalars().all()
    by_id = {p.id: p for p in rows}
    ordered = [by_id[i] for i in ids if i in by_id]
    if len(ordered) < 2:
        raise HTTPException(status_code=422, detail={"code": "route.too_few_stops"})
    return ordered


@router.post("/plan-week", response_model=PlanWeekOut)
async def plan_week(
    body: PlanWeekBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("settlements")),
):
    """Az esedékes megállók szétosztása napokra + naponkénti sorrendezés.

    Földrajzi „sweep" klaszterezés: a megállókat a súlypont körüli szög
    szerint rendezzük, és így vágjuk kiegyenlített, szomszédos csoportokra —
    egy nap megállói egy irányba esnek. A koordináta nélküli partnerek az
    utolsó nap végére kerülnek."""
    partners = await _load_route_partners(db, body.partner_ids)

    located: list[tuple[Partner, tuple[float, float]]] = []
    unlocated: list[Partner] = []
    for p in partners:
        coords = _partner_coords(p)
        (located.append((p, coords)) if coords else unlocated.append(p))

    def _address(p: Partner) -> str | None:
        if p.address:
            return p.address
        parts = [x for x in (p.address_zip, p.address_city, p.address_street, p.address_number) if x]
        return " ".join(parts) or None

    def _stop(p: Partner, ok: bool) -> RouteStopOut:
        return RouteStopOut(partner_id=str(p.id), name=p.name, address=_address(p), located=ok)

    used_roads = False
    day_groups: list[list[tuple[Partner, tuple[float, float]]]] = []
    if located:
        clat = sum(c[0] for _, c in located) / len(located)
        clng = sum(c[1] for _, c in located) / len(located)
        swept = sorted(
            located, key=lambda pc: math.atan2(pc[1][0] - clat, pc[1][1] - clng)
        )
        n, days = len(swept), min(body.days, len(swept))
        base, extra = divmod(n, days)
        pos = 0
        for d in range(days):
            size = base + (1 if d < extra else 0)
            day_groups.append(swept[pos:pos + size])
            pos += size

        all_coords = [c for _, c in swept]
        road = await _osrm_matrix(all_coords) if len(all_coords) >= 2 else None
        if road is not None:
            full_dist = road[0]
            used_roads = True
        else:
            full_dist = _haversine_matrix(all_coords)
        index_of = {id(pc): i for i, pc in enumerate(swept)}

        ordered_groups = []
        for group in day_groups:
            if len(group) >= 2:
                idxs = [index_of[id(pc)] for pc in group]
                sub = [[full_dist[a][b] for b in idxs] for a in idxs]
                order = _order_stops(sub)
                ordered_groups.append(([group[i] for i in order],
                                       _path_km(sub, order)))
            else:
                ordered_groups.append((group, 0.0 if group else None))
        day_groups_out = ordered_groups
    else:
        day_groups_out = []

    days_out: list[PlanDayOut] = []
    for group, km in day_groups_out:
        days_out.append(PlanDayOut(stops=[_stop(p, True) for p, _c in group], total_km=km))
    if unlocated:
        if not days_out:
            days_out.append(PlanDayOut(stops=[], total_km=None))
        days_out[-1].stops.extend(_stop(p, False) for p in unlocated)
    total = round(sum(d.total_km or 0.0 for d in days_out), 1) if days_out else None
    return PlanWeekOut(days=days_out, total_km=total, used_roads=used_roads)
