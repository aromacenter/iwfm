"""GLS Hungary (MyGLS) integráció.

Címke-készítés (PrintLabels) és csomagkövetés (GetParcelStatuses) a MyGLS
JSON API-n. A jelszó SHA512-hash-ként, bájt-tömbként megy minden kérésben
(a MyGLS így várja); a hitelesítő adatok a Beállításokban tárolódnak
(Fernet-titkosított jelszó, write-only).

Teszt-mód: api.test.mygls.hu — a valódi feladás előtt itt érdemes próbálni.
"""

from __future__ import annotations

import hashlib
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_pii
from app.models import GlsSettings

logger = logging.getLogger(__name__)

BASE_PROD = "https://api.mygls.hu/ParcelService.svc/json"
BASE_TEST = "https://api.test.mygls.hu/ParcelService.svc/json"

PRINTER_TYPES = ("A4_2x2", "A4_4x1", "Thermo")


async def load_gls_config(db: AsyncSession) -> dict:
    """A GLS-beállítások betöltése; ValueError, ha nincs beállítva."""
    row = (
        await db.execute(select(GlsSettings).where(GlsSettings.id == 1))
    ).scalar_one_or_none()
    if row is None or not row.username or not row.password_encrypted or not row.client_number:
        raise ValueError("gls.not_configured")
    password = decrypt_pii(row.password_encrypted)
    if not password:
        raise ValueError("gls.not_configured")
    return {
        "username": row.username,
        "password": password,
        "client_number": row.client_number,
        "test_mode": row.test_mode,
        "printer_type": row.printer_type if row.printer_type in PRINTER_TYPES else "A4_2x2",
        "sender": {
            "Name": row.sender_name or "",
            "Street": row.sender_street or "",
            "HouseNumber": row.sender_house or "",
            "City": row.sender_city or "",
            "ZipCode": row.sender_zip or "",
            "CountryIsoCode": "HU",
            "ContactName": row.sender_name or "",
            "ContactPhone": row.sender_phone or "",
            "ContactEmail": row.sender_email or "",
        },
    }


def _password_bytes(password: str) -> list[int]:
    """A MyGLS a jelszó SHA512-hash-ét bájt-tömbként (int-lista) várja."""
    return list(hashlib.sha512(password.encode("utf-8")).digest())


async def _call(cfg: dict, method: str, payload: dict) -> dict:
    base = BASE_TEST if cfg["test_mode"] else BASE_PROD
    body = {
        "Username": cfg["username"],
        "Password": _password_bytes(cfg["password"]),
        **payload,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(f"{base}/{method}", json=body)
    if res.status_code != 200:
        logger.warning("MyGLS %s HTTP %s: %s", method, res.status_code, res.text[:300])
        raise RuntimeError(f"gls.http_{res.status_code}")
    return res.json()


def _format_errors(errors: list[dict]) -> str:
    out = []
    for e in errors or []:
        desc = e.get("ErrorDescription") or e.get("ErrorCode") or "ismeretlen hiba"
        out.append(str(desc))
    return "; ".join(out) or "ismeretlen GLS-hiba"


async def create_label(
    db: AsyncSession,
    *,
    recipient: dict,
    content: str | None,
    count: int,
    cod_amount: float | None,
    cod_reference: str | None,
    client_reference: str | None,
) -> tuple[str, bytes]:
    """Címke készítése (PrintLabels). Visszaad: (csomagszám, címke-PDF).

    ``recipient`` kulcsai: name, zip, city, street, house, phone, email.
    Utánvétnél a CODAmount a beszedendő BRUTTÓ összeg (Ft).
    """
    cfg = await load_gls_config(db)
    parcel: dict = {
        "ClientNumber": int(cfg["client_number"]),
        "ClientReference": (client_reference or "")[:40],
        "Content": (content or "")[:100],
        "Count": max(1, int(count or 1)),
        "PickupAddress": cfg["sender"],
        "DeliveryAddress": {
            "Name": recipient["name"][:255],
            "Street": recipient["street"][:255],
            "HouseNumber": (recipient.get("house") or "")[:32],
            "City": recipient["city"][:128],
            "ZipCode": recipient["zip"][:16],
            "CountryIsoCode": "HU",
            "ContactName": recipient["name"][:255],
            "ContactPhone": recipient.get("phone") or "",
            "ContactEmail": recipient.get("email") or "",
        },
        "ServiceList": [],
    }
    if cod_amount and cod_amount > 0:
        parcel["CODAmount"] = float(cod_amount)
        parcel["CODCurrency"] = "HUF"
        if cod_reference:
            parcel["CODReference"] = cod_reference[:40]
    # kézbesítési e-mail értesítés (FDS), ha van címzett-e-mail
    if recipient.get("email"):
        parcel["ServiceList"].append(
            {"Code": "FDS", "FDSParameter": {"Value": recipient["email"]}}
        )

    data = await _call(
        cfg, "PrintLabels",
        {"ParcelList": [parcel], "TypeOfPrinter": cfg["printer_type"]},
    )
    errors = data.get("PrintLabelsErrorList") or []
    if errors:
        raise RuntimeError(_format_errors(errors))
    info = (data.get("PrintLabelsInfoList") or [{}])[0]
    parcel_number = str(info.get("ParcelNumber") or "")
    gls_parcel_id = info.get("ParcelId")
    label_bytes = bytes(data.get("Labels") or [])
    if not parcel_number or not label_bytes:
        raise RuntimeError("gls.empty_response")
    return parcel_number, gls_parcel_id, label_bytes


def normalize_status(events: list[dict]) -> str:
    """Az 5 fix státusz egyike az esemény-idővonalból:
    created | handed_over | in_transit | delivered | returned."""
    if not events:
        return "created"
    latest = str(events[0].get("description") or "").lower()
    if "vissza" in latest:  # visszáru / visszaküldés / visszaszállítás
        return "returned"
    if "kézbesít" in latest or "kezbesit" in latest or "delivered" in latest:
        return "delivered"
    if len(events) == 1:
        return "handed_over"  # az első GLS-esemény: a futár/depó átvette
    return "in_transit"


async def get_statuses(db: AsyncSession, parcel_number: str) -> list[dict]:
    """A csomag TELJES esemény-idővonala (GetParcelStatuses), legújabb elöl:
    [{date, description, depot, code}]."""
    cfg = await load_gls_config(db)
    data = await _call(
        cfg, "GetParcelStatuses",
        {
            "ParcelNumber": int(parcel_number),
            "ReturnPOD": False,
            "LanguageIsoCode": "HU",
        },
    )
    err = data.get("GetParcelStatusErrors") or []
    if err:
        raise RuntimeError(_format_errors(err))
    return [
        {
            "date": str(s.get("StatusDate") or ""),
            "description": str(s.get("StatusDescription") or s.get("StatusCode") or "?"),
            "depot": str(s.get("DepotCity") or ""),
            "code": str(s.get("StatusCode") or ""),
        }
        for s in (data.get("ParcelStatusList") or [])
    ]


async def delete_label(db: AsyncSession, gls_parcel_id: int) -> None:
    """Címke törlése a MyGLS-nél (DeleteLabels) — csak addig lehetséges, amíg
    a csomagot nem adtuk át a futárnak; utána a GLS elutasítja."""
    cfg = await load_gls_config(db)
    data = await _call(cfg, "DeleteLabels", {"ParcelIdList": [int(gls_parcel_id)]})
    errors = data.get("DeleteLabelsErrorList") or []
    if errors:
        raise RuntimeError(_format_errors(errors))
