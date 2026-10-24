"""Partner-cím geokódolás (Nominatim / OpenStreetMap).

Best-effort: hálózati vagy szolgáltatás-hiba esetén None-t adunk vissza, a
partner-mentést soha nem akadályozza. A hívás kikapcsolható a WFM_GEOCODE=0
env változóval (tesztek alatt így fut). Használati szabály (Nominatim policy):
azonosító User-Agent + legfeljebb 1 kérés/másodperc — partner-mentésnél
egyetlen kérés megy, a tömeges backfill külön szkriptben, késleltetéssel fut.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_UA = "Iwfm/1.0 (hello@aromacenter.hu)"


def _base_url() -> str:
    return os.environ.get("WFM_NOMINATIM_URL", "https://nominatim.openstreetmap.org")


def enabled() -> bool:
    return os.environ.get("WFM_GEOCODE", "1") != "0"


async def geocode_address(
    zip_code: str | None,
    city: str | None,
    street: str | None,
    number: str | None,
    fallback_text: str | None = None,
) -> tuple[float, float] | None:
    """Cím → (lat, lng) vagy None. Előbb strukturált keresés, majd szabad
    szöveges; mindkettő Magyarországra szűkítve."""
    if not enabled():
        return None
    queries: list[dict] = []
    if city and street:
        q = {"postalcode": zip_code or "", "city": city,
             "street": f"{street} {number or ''}".strip()}
        queries.append({k: v for k, v in q.items() if v})
    if fallback_text:
        queries.append({"q": fallback_text})
    elif city:
        queries.append({"q": f"{zip_code or ''} {city}".strip()})

    for params in queries:
        params.update({"format": "jsonv2", "limit": "1", "countrycodes": "hu"})
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": _UA, "Referer": "https://program.x-presso.hu/"}) as cx:
                res = await cx.get(f"{_base_url()}/search", params=params)
            if res.status_code in (403, 429):
                logger.warning("Nominatim %s — átváltás a tartalék geokódolóra (Photon)", res.status_code)
                return await _photon(params.get("q") or " ".join(str(v) for v in params.values() if v and v not in ("jsonv2", "1", "hu")))
            res.raise_for_status()
            items = res.json()
            if items:
                return (float(items[0]["lat"]), float(items[0]["lon"]))
        except Exception:  # pragma: no cover - hálózati hiba: best-effort
            logger.warning("Geokódolás sikertelen (%s)", params, exc_info=True)
            return None
    # strukturált + szabad keresés nélkül: tartalék
    text = fallback_text or " ".join(x for x in (zip_code, city, street, number) if x)
    return await _photon(text) if text else None


async def _photon(text: str) -> tuple[float, float] | None:
    """Tartalék geokódoló (Photon / komoot, OSM-adat, kulcs nélkül) — ha a
    Nominatim 403/429-et ad (7836fee3)."""
    url = os.environ.get("WFM_PHOTON_URL", "https://photon.komoot.io/api/")
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": _UA}) as cx:
            res = await cx.get(url, params={"q": f"{text}, Magyarország", "limit": "1", "lang": "de"})
        res.raise_for_status()
        feats = (res.json() or {}).get("features") or []
        if feats:
            lon, lat = feats[0]["geometry"]["coordinates"][:2]
            return (float(lat), float(lon))
    except Exception:  # pragma: no cover
        logger.warning("Photon geokódolás sikertelen (%s)", text, exc_info=True)
    return None


async def geocode_partner_background(partner_id) -> None:
    """Háttérfeladat: a partner címének geokódolása és mentése saját
    session-ben. Hiba esetén csendben kimarad."""
    from sqlalchemy import select

    from app.db import get_session_factory
    from app.models import Partner

    try:
        factory = get_session_factory()
        async with factory() as db:
            p = (
                await db.execute(select(Partner).where(Partner.id == partner_id))
            ).scalar_one_or_none()
            if p is None:
                return
            coords = await geocode_address(
                p.address_zip, p.address_city, p.address_street, p.address_number,
                fallback_text=p.address,
            )
            if coords is not None:
                p.lat, p.lng = coords
                await db.commit()
    except Exception:  # pragma: no cover - best-effort háttérfeladat
        logger.info("Partner-geokódolás háttérben sikertelen", exc_info=True)


def schedule_geocode(partner_id) -> None:
    """Fire-and-forget geokódolás a partner mentése után."""
    if not enabled():
        return
    try:
        asyncio.get_running_loop().create_task(geocode_partner_background(partner_id))
    except RuntimeError:  # pragma: no cover - nincs futó event loop
        pass
