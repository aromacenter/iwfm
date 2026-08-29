"""Egységes futár-réteg: MPL, FoxPost, DPD adapterek + diszpécser.

A GLS a saját, bejáratott gls_service-én megy; ez a modul a többi futárt
hozza ugyanarra a felületre: create_label / get_statuses / delete_parcel.
A hitelesítő adatok futáronként a courier_settings táblában, Fernet-
titkosított JSON-ban élnek (write-only, a felület sosem kapja vissza őket).

A külső hívások bőbeszédűen logolnak — az első éles címkénél a futár-API-k
válaszaiból derül ki, hol kell finomítani (ahogy a MyGLS-nél is történt).
"""

from __future__ import annotations

import base64
import json
import logging
import time as _time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_pii, encrypt_pii
from app.models import CourierSettings, GlsSettings

logger = logging.getLogger(__name__)

CARRIERS = ("gls", "mpl", "foxpost", "dpd")

# futáronként a titkos mezők — a GET ezek helyett csak has_* jelzőt ad vissza
SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    "mpl": ("client_secret",),
    "foxpost": ("password", "api_key"),
    "dpd": ("password", "api_key"),
}
# a beállítás-űrlap mezői futáronként (a titkosak is itt szerepelnek)
CONFIG_FIELDS: dict[str, tuple[str, ...]] = {
    "mpl": ("client_id", "client_secret", "accounting_code", "agreement",
            "basic_service", "test_mode"),
    "foxpost": ("username", "password", "api_key", "test_mode"),
    "dpd": ("username", "password", "api_key", "test_mode"),
}

MPL_BASE = "https://core.api.posta.hu"
FOXPOST_BASE = "https://webapi.foxpost.hu"
FOXPOST_TEST_BASE = "https://webapi-test.foxpost.hu"
DPD_BASE = "https://weblabel.dpd.hu/dpd_wow"


async def load_config(db: AsyncSession, carrier: str) -> dict:
    """A futár titkosított konfigurációja; ValueError, ha nincs beállítva."""
    row = (
        await db.execute(
            select(CourierSettings).where(CourierSettings.carrier == carrier)
        )
    ).scalar_one_or_none()
    if row is None or not row.config_encrypted:
        raise ValueError("courier.not_configured")
    try:
        cfg = json.loads(decrypt_pii(bytes(row.config_encrypted)) or "{}")
    except Exception:
        raise ValueError("courier.not_configured")
    if not isinstance(cfg, dict) or not cfg:
        raise ValueError("courier.not_configured")
    return cfg


async def save_config(db: AsyncSession, carrier: str, updates: dict) -> dict:
    """Konfiguráció mentése: az üresen hagyott titkos mező a régi marad,
    a "-" érték töröl. Visszaadja az új (nyers) konfigurációt."""
    row = (
        await db.execute(
            select(CourierSettings).where(CourierSettings.carrier == carrier)
        )
    ).scalar_one_or_none()
    current: dict = {}
    if row is not None and row.config_encrypted:
        try:
            current = json.loads(decrypt_pii(bytes(row.config_encrypted)) or "{}")
        except Exception:
            current = {}
    secrets_ = SECRET_FIELDS.get(carrier, ())
    for key in CONFIG_FIELDS.get(carrier, ()):
        if key not in updates:
            continue
        value = updates[key]
        if key in secrets_:
            if value is None or value == "":
                continue  # üres = marad a régi
            if isinstance(value, str) and value.strip() == "-":
                current.pop(key, None)
                continue
        current[key] = value.strip() if isinstance(value, str) else value
    if row is None:
        row = CourierSettings(carrier=carrier)
        db.add(row)
    row.config_encrypted = encrypt_pii(json.dumps(current))
    row.updated_at = datetime.now(UTC)
    return current


def masked_config(carrier: str, cfg: dict | None) -> dict:
    """A felületnek visszaadható forma: a titkos mezők helyett has_* jelző."""
    cfg = cfg or {}
    out: dict = {}
    for key in CONFIG_FIELDS.get(carrier, ()):
        if key in SECRET_FIELDS.get(carrier, ()):
            out[f"has_{key}"] = bool(cfg.get(key))
        else:
            out[key] = cfg.get(key)
    return out


# ─── MPL (Magyar Posta) ─────────────────────────────────────────────────────

_mpl_token: dict = {"value": None, "expires": 0.0, "key": None}


async def _mpl_get_token(cfg: dict) -> str:
    cache_key = cfg.get("client_id")
    if (
        _mpl_token["value"]
        and _mpl_token["key"] == cache_key
        and _time.monotonic() < _mpl_token["expires"]
    ):
        return _mpl_token["value"]
    basic = base64.b64encode(
        f"{cfg['client_id']}:{cfg['client_secret']}".encode()
    ).decode()
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            f"{MPL_BASE}/oauth2/token",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            content="grant_type=client_credentials",
        )
    if res.status_code != 200:
        logger.warning("MPL token HTTP %s: %s", res.status_code, res.text[:200])
        raise RuntimeError(f"mpl.auth_{res.status_code}")
    data = res.json()
    _mpl_token["value"] = data["access_token"]
    _mpl_token["key"] = cache_key
    _mpl_token["expires"] = _time.monotonic() + max(60, int(data.get("expires_in", 1800)) - 120)
    return _mpl_token["value"]


async def _mpl_call(cfg: dict, method: str, path: str, json_body=None) -> httpx.Response:
    token = await _mpl_get_token(cfg)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Accounting-Code": str(cfg.get("accounting_code") or ""),
        "Content-Type": "application/json;charset=UTF-8",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.request(
            method, f"{MPL_BASE}{path}", headers=headers, json=json_body
        )
    if res.status_code >= 400:
        logger.warning("MPL %s %s HTTP %s: %s", method, path, res.status_code, res.text[:400])
        raise RuntimeError(f"MPL HTTP {res.status_code}: {res.text[:200]}")
    return res


def _mpl_sender(gls_row: GlsSettings | None, cfg: dict) -> dict:
    """A feladó a GLS-beállítások Feladó-blokkjából jön — egy helyen tartjuk."""
    s = gls_row
    return {
        "agreement": str(cfg.get("agreement") or ""),
        "contact": {
            "name": (s.sender_name if s else "") or "",
            "email": (s.sender_email if s else "") or "",
            "phone": (s.sender_phone if s else "") or "",
        },
        "address": {
            "postCode": (s.sender_zip if s else "") or "",
            "city": (s.sender_city if s else "") or "",
            "address": f"{(s.sender_street if s else '') or ''} {(s.sender_house if s else '') or ''}".strip(),
        },
    }


async def mpl_create(db: AsyncSession, cfg: dict, *, recipient: dict, content, count,
                     cod_amount, cod_reference, client_reference, weight_g: int) -> dict:
    gls_row = (
        await db.execute(select(GlsSettings).where(GlsSettings.id == 1))
    ).scalar_one_or_none()
    item: dict = {
        "weight": {"value": max(1, int(weight_g or 1000)), "unit": "G"},
        "services": {"basic": cfg.get("basic_service") or "A_175_UZL", "extra": []},
    }
    if cod_amount and cod_amount > 0:
        item["services"]["extra"].append("K_UVT")
        item["services"]["cod"] = int(cod_amount)
    shipment = {
        "sender": _mpl_sender(gls_row, cfg),
        "orderId": (client_reference or cod_reference or "")[:32] or None,
        "labelType": "A5",
        "item": [item] * max(1, int(count or 1)),
        "recipient": {
            "contact": {
                "name": recipient["name"][:255],
                "email": recipient.get("email") or "",
                "phone": recipient.get("phone") or "",
            },
            "address": {
                "postCode": recipient["zip"],
                "city": recipient["city"],
                "address": f"{recipient['street']} {recipient.get('house') or ''}".strip(),
            },
        },
    }
    res = await _mpl_call(cfg, "POST", "/v2/mplapi/shipments", [shipment])
    data = res.json()
    first = (data or [{}])[0]
    errors = first.get("errors")
    if errors:
        raise RuntimeError("; ".join(str(e) for e in errors)[:300])
    tracking = str(first.get("trackingNumber") or "")
    if not tracking:
        raise RuntimeError("mpl.no_tracking_number")
    label_b64 = first.get("label")
    if not label_b64:
        lres = await _mpl_call(
            cfg, "GET", f"/v2/mplapi/shipments/label?trackingNumbers={tracking}"
        )
        try:
            ldata = lres.json()
            entry = ldata[0] if isinstance(ldata, list) else ldata
            label_b64 = entry.get("label") if isinstance(entry, dict) else None
        except Exception:
            label_b64 = None
    label_pdf = base64.b64decode(label_b64) if label_b64 else None
    return {"tracking_number": tracking, "carrier_ref": tracking, "label_pdf": label_pdf}


async def mpl_statuses(db: AsyncSession, cfg: dict, tracking: str, ref) -> list[dict]:
    res = await _mpl_call(cfg, "GET", f"/v2/mplapi/shipments/{tracking}")
    try:
        data = res.json()
    except Exception:
        return []
    entry = data[0] if isinstance(data, list) and data else data
    if not isinstance(entry, dict):
        return []
    ship = entry.get("shipment") or entry
    status = ship.get("status") or ship.get("state")
    if not status:
        return []
    return [{"date": str(ship.get("shipmentDate") or ""), "description": str(status),
             "depot": "", "code": ""}]


async def mpl_delete(db: AsyncSession, cfg: dict, tracking: str, ref) -> None:
    await _mpl_call(cfg, "DELETE", f"/v2/mplapi/shipments/{tracking}")


# ─── FoxPost ────────────────────────────────────────────────────────────────


def _foxpost_base(cfg: dict) -> str:
    return FOXPOST_TEST_BASE if cfg.get("test_mode") else FOXPOST_BASE


def _foxpost_headers(cfg: dict) -> dict:
    basic = base64.b64encode(f"{cfg['username']}:{cfg['password']}".encode()).decode()
    return {"Authorization": f"Basic {basic}", "api-key": cfg["api_key"]}


async def foxpost_create(db: AsyncSession, cfg: dict, *, recipient: dict, content, count,
                         cod_amount, cod_reference, client_reference, weight_g) -> dict:
    parcel: dict = {
        "recipientName": recipient["name"][:100],
        "recipientPhone": recipient.get("phone") or "",
        "recipientEmail": recipient.get("email") or "",
        "size": "M",
        "cod": int(cod_amount) if cod_amount and cod_amount > 0 else 0,
        "refCode": (client_reference or cod_reference or "")[:30] or None,
    }
    if recipient.get("apm_id"):  # csomagautomatás kézbesítés
        parcel["destination"] = recipient["apm_id"]
    else:  # házhozszállítás
        parcel.update({
            "recipientZip": recipient["zip"],
            "recipientCity": recipient["city"],
            "recipientAddress": f"{recipient['street']} {recipient.get('house') or ''}".strip(),
        })
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(
            f"{_foxpost_base(cfg)}/api/parcel", headers=_foxpost_headers(cfg),
            json=[parcel],
        )
    if res.status_code >= 400:
        logger.warning("FoxPost parcel HTTP %s: %s", res.status_code, res.text[:400])
        raise RuntimeError(f"FoxPost HTTP {res.status_code}: {res.text[:200]}")
    data = res.json()
    entry = (data.get("parcels") if isinstance(data, dict) else data) or [{}]
    first = entry[0] if isinstance(entry, list) else entry
    fox_id = first.get("clFoxId") or first.get("clfoxid")
    barcode = first.get("clBarcode") or first.get("barcode") or str(fox_id or "")
    if not fox_id:
        raise RuntimeError(f"foxpost.no_parcel_id: {str(data)[:200]}")
    # címke PDF
    async with httpx.AsyncClient(timeout=60) as client:
        lres = await client.post(
            f"{_foxpost_base(cfg)}/api/label/A7", headers=_foxpost_headers(cfg),
            json=[int(fox_id)],
        )
    label_pdf = lres.content if lres.status_code == 200 and lres.content[:4] == b"%PDF" else None
    if label_pdf is None:
        logger.warning("FoxPost label HTTP %s: %s", lres.status_code, lres.text[:200])
    return {"tracking_number": str(barcode), "carrier_ref": str(fox_id), "label_pdf": label_pdf}


async def foxpost_statuses(db: AsyncSession, cfg: dict, tracking: str, ref) -> list[dict]:
    ident = ref or tracking
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(
            f"{_foxpost_base(cfg)}/api/tracking/{ident}", headers=_foxpost_headers(cfg),
        )
    if res.status_code >= 400:
        raise RuntimeError(f"FoxPost tracking HTTP {res.status_code}")
    data = res.json()
    traces = data.get("traces") or data.get("trackings") or []
    events = [
        {
            "date": str(t.get("statusDate") or t.get("date") or ""),
            "description": str(t.get("longStatus") or t.get("status") or "?"),
            "depot": str(t.get("place") or ""),
            "code": str(t.get("shortStatus") or ""),
        }
        for t in traces
    ]
    events.reverse()  # legújabb elöl
    return events


async def foxpost_delete(db: AsyncSession, cfg: dict, tracking: str, ref) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.delete(
            f"{_foxpost_base(cfg)}/api/parcel/{ref or tracking}",
            headers=_foxpost_headers(cfg),
        )
    if res.status_code >= 400:
        raise RuntimeError(f"FoxPost delete HTTP {res.status_code}: {res.text[:150]}")


# ─── DPD (weblabel) ─────────────────────────────────────────────────────────


async def _dpd_post(cfg: dict, path: str, data: dict) -> dict:
    payload = {"username": cfg["username"], "password": cfg["password"], **data}
    if cfg.get("api_key"):
        payload["api_key"] = cfg["api_key"]
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(f"{DPD_BASE}/{path}", data=payload)
    if res.status_code >= 400:
        logger.warning("DPD %s HTTP %s: %s", path, res.status_code, res.text[:300])
        raise RuntimeError(f"DPD HTTP {res.status_code}")
    try:
        out = res.json()
    except Exception:
        raise RuntimeError(f"dpd.bad_response: {res.text[:150]}")
    if str(out.get("status", "")).lower() not in ("ok", "success", ""):
        raise RuntimeError(f"DPD: {str(out.get('errlog') or out.get('error') or out)[:200]}")
    return out


async def dpd_create(db: AsyncSession, cfg: dict, *, recipient: dict, content, count,
                     cod_amount, cod_reference, client_reference, weight_g) -> dict:
    data = {
        "name1": recipient["name"][:35],
        "street": f"{recipient['street']} {recipient.get('house') or ''}".strip()[:35],
        "city": recipient["city"][:35],
        "pcode": recipient["zip"],
        "country": "HU",
        "phone": recipient.get("phone") or "",
        "email": recipient.get("email") or "",
        "weight": max(0.1, round((weight_g or 1000) / 1000, 2)),
        "num_of_parcel": max(1, int(count or 1)),
        "parcel_type": "D-COD" if (cod_amount and cod_amount > 0) else "D",
        "order_number": (client_reference or "")[:25],
        "remark": (content or "")[:100],
    }
    if cod_amount and cod_amount > 0:
        data["cod_amount"] = int(cod_amount)
        data["cod_purpose"] = (cod_reference or client_reference or "")[:25]
    out = await _dpd_post(cfg, "parcel_import.php", data)
    numbers = out.get("pl_number") or []
    if isinstance(numbers, str):
        numbers = [numbers]
    if not numbers:
        raise RuntimeError(f"dpd.no_parcel_number: {str(out)[:150]}")
    tracking = str(numbers[0])
    # címke PDF
    label_pdf = None
    try:
        pres = await _dpd_post(cfg, "parcel_print.php", {"parcel_numbers": "|".join(map(str, numbers))})
        b64 = pres.get("pdf")
        if b64:
            label_pdf = base64.b64decode(b64)
    except RuntimeError as exc:
        logger.warning("DPD cimke-hiba: %s", exc)
    return {"tracking_number": tracking, "carrier_ref": tracking, "label_pdf": label_pdf}


async def dpd_statuses(db: AsyncSession, cfg: dict, tracking: str, ref) -> list[dict]:
    out = await _dpd_post(cfg, "parcel_status.php", {"parcel_number": tracking})
    status = out.get("parcel_status") or out.get("status_text")
    if not status:
        return []
    return [{"date": "", "description": str(status), "depot": "", "code": ""}]


async def dpd_delete(db: AsyncSession, cfg: dict, tracking: str, ref) -> None:
    await _dpd_post(cfg, "parcel_delete.php", {"parcel_numbers": str(tracking)})


# ─── Diszpécser ─────────────────────────────────────────────────────────────

_ADAPTERS = {
    "mpl": (mpl_create, mpl_statuses, mpl_delete),
    "foxpost": (foxpost_create, foxpost_statuses, foxpost_delete),
    "dpd": (dpd_create, dpd_statuses, dpd_delete),
}


async def create_label(db: AsyncSession, carrier: str, **kwargs) -> dict:
    cfg = await load_config(db, carrier)
    result = await _ADAPTERS[carrier][0](db, cfg, **kwargs)
    result.setdefault("test_mode", bool(cfg.get("test_mode")))
    return result


async def get_statuses(db: AsyncSession, carrier: str, tracking: str, ref) -> list[dict]:
    cfg = await load_config(db, carrier)
    return await _ADAPTERS[carrier][1](db, cfg, tracking, ref)


async def delete_parcel(db: AsyncSession, carrier: str, tracking: str, ref) -> None:
    cfg = await load_config(db, carrier)
    await _ADAPTERS[carrier][2](db, cfg, tracking, ref)


def normalize_status(events: list[dict]) -> str:
    """Az 5 fix státusz — a GLS-es heurisztika általánosítva magyar és angol
    futár-szövegekre."""
    if not events:
        return "created"
    latest = str(events[0].get("description") or "").lower()
    if "vissza" in latest or "return" in latest:
        return "returned"
    if ("kézbesít" in latest or "kezbesit" in latest or "delivered" in latest
            or "átvéve" in latest or "atveve" in latest or "kiszállítva" in latest):
        return "delivered"
    if len(events) == 1:
        return "handed_over"
    return "in_transit"
