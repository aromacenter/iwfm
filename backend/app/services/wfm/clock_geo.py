"""Helyadat a blokkolásnál (191. kör).

A böngésző (kioszk-tablet vagy a dolgozó telefonja) a dolgozó engedélyével
adja át a koordinátát; a szerver a legközelebbi ISMERT helyhez (saját
telephely a beállításokból, vagy geokódolt partner) méri a távolságot és a
bejegyzéshez rögzíti. Folyamatos követés nincs — csak a blokkolás pillanata
(Mt. 11/A §: a dolgozót előre, írásban tájékoztatni kell).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MODES = ("off", "optional", "required")
MAX_SITES = 20
DEFAULT_RADIUS_M = 300


class GeoIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=100_000)


@dataclass
class ClockRules:
    mode: str = "off"
    radius_m: int = DEFAULT_RADIUS_M
    phone_clock: bool = False
    sites: list[dict] = field(default_factory=list)


async def clock_rules(db: AsyncSession) -> ClockRules:
    """A hatályos szabályok — a licenc-modul nélkül minden KI."""
    from app.models import WorkRulesSettings
    from app.services.wfm.license import module_enabled

    if not await module_enabled(db, "clock_location"):
        return ClockRules()
    row = (await db.execute(select(WorkRulesSettings).where(WorkRulesSettings.id == 1))).scalar_one_or_none()
    if row is None:
        return ClockRules()
    mode = row.clock_location if row.clock_location in MODES else "off"
    return ClockRules(
        mode=mode,
        radius_m=int(row.clock_radius_m or DEFAULT_RADIUS_M),
        phone_clock=bool(row.phone_clock),
        sites=[s for s in (row.clock_sites or []) if isinstance(s, dict)],
    )


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def normalize_sites(raw) -> list[dict]:
    """Beállításból jövő telephely-lista tisztítása (név, cím, koordináta)."""
    out: list[dict] = []
    for item in (raw or [])[:MAX_SITES]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:128]
        address = str(item.get("address") or "").strip()[:256]
        if not name and not address:
            continue
        lat, lng = _num(item.get("lat")), _num(item.get("lng"))
        if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
            lat = lng = None
        out.append({"name": name or address, "address": address, "lat": lat, "lng": lng})
    return out


async def geocode_sites(sites: list[dict], previous: list[dict] | None = None) -> list[dict]:
    """Koordináta nélküli telephelyek geokódolása cím alapján (best-effort).
    Ha a cím nem változott, az előző koordináta marad."""
    from app.services.wfm.geocode import geocode_address

    prev = {(s.get("name"), s.get("address")): s for s in (previous or []) if isinstance(s, dict)}
    for s in sites:
        if s["lat"] is not None:
            continue
        old = prev.get((s["name"], s["address"]))
        if old and _num(old.get("lat")) is not None and _num(old.get("lng")) is not None:
            s["lat"], s["lng"] = float(old["lat"]), float(old["lng"])
            continue
        if not s["address"]:
            continue
        try:
            coords = await geocode_address(None, None, None, None, fallback_text=s["address"])
        except Exception:  # pragma: no cover - hálózat
            coords = None
        if coords:
            s["lat"], s["lng"] = coords
    return sites


async def nearest_place(db: AsyncSession, lat: float, lng: float, sites: list[dict]) -> tuple[str | None, float | None]:
    """A legközelebbi ismert hely (saját telephely vagy geokódolt partner) és a távolság méterben."""
    from app.models import Partner

    best_label: str | None = None
    best_dist: float | None = None
    for s in sites:
        slat, slng = _num(s.get("lat")), _num(s.get("lng"))
        if slat is None or slng is None:
            continue
        d = haversine_m(lat, lng, slat, slng)
        if best_dist is None or d < best_dist:
            best_label, best_dist = str(s.get("name") or s.get("address") or "Telephely"), d
    rows = (
        await db.execute(
            select(Partner.name, Partner.address_city, Partner.lat, Partner.lng)
            .where(Partner.lat.is_not(None), Partner.lng.is_not(None))
        )
    ).all()
    for name, city, plat, plng in rows:
        d = haversine_m(lat, lng, float(plat), float(plng))
        if best_dist is None or d < best_dist:
            best_label, best_dist = (f"{name} ({city})" if city else str(name)), d
    return best_label, best_dist


async def build_geo(db: AsyncSession, geo: GeoIn, rules: ClockRules, src: str) -> dict:
    """A bejegyzésre kerülő helyadat-pillanatkép. ``far``: van ismert hely, és a
    távolság a tűrésen (+ a mérés pontatlansága, legfeljebb a tűrés) kívül esik;
    None, ha nincs mihez mérni."""
    label, dist = await nearest_place(db, geo.lat, geo.lng, rules.sites)
    acc = round(geo.accuracy_m) if geo.accuracy_m is not None else None
    far: bool | None = None
    if dist is not None:
        far = dist > rules.radius_m + min(acc or 0, rules.radius_m)
    return {
        "lat": round(geo.lat, 6),
        "lng": round(geo.lng, 6),
        "acc": acc,
        "site": label,
        "dist_m": round(dist) if dist is not None else None,
        "far": far,
        "src": src,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def map_link(geo: dict) -> str:
    return f"https://www.openstreetmap.org/?mlat={geo['lat']}&mlon={geo['lng']}#map=17/{geo['lat']}/{geo['lng']}"


async def alert_far(db: AsyncSession, employee, action: str, geo: dict, entry_id) -> None:
    """Telegram-esemény (ha be van pipálva) + automatizálás: a dolgozó a
    tűrésen kívül blokkolt. Best-effort."""
    if not geo.get("far"):
        return
    try:
        from app.services.wfm.automation import fire_event
        from app.services.wfm.telegram import notify_event

        ctx = {
            "dolgozo": f"{employee.last_name} {employee.first_name}",
            "muvelet": "beblokkolás" if action == "in" else "kiblokkolás",
            "idopont": datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M"),
            "tavolsag_m": geo.get("dist_m"),
            "legkozelebbi": geo.get("site") or "—",
            "pontossag_m": geo.get("acc") if geo.get("acc") is not None else "?",
            "forras": "telefon" if geo.get("src") == "phone" else "terminál",
            "terkep": map_link(geo),
            "_entry_id": str(entry_id),
        }
        fire_event("timeclock.far", ctx)
        await notify_event(db, "timeclock.far", ctx)
    except Exception:  # noqa: BLE001
        logger.warning("far-clock alert failed", exc_info=True)
