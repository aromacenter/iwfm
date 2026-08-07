"""Billingó v3 API integráció — elszámolás kiszámlázása.

A kulcs Fernet-titkosítva a billingo_settings sorban (id=1). Teszt-módban
díjbekérőt (proforma) hozunk létre — az NEM kerül a NAV-hoz; éles módban
számlát (invoice). A partner adatai (név, cím, adószám) a Partner törzsből
mennek. A hívás best-effort hibakezelésű: a settlement invoiced marad False,
ha a Billingó hívás elbukik.

API: https://api.billingo.hu/v3 (X-API-KEY fejléc).
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_pii
from app.models import BillingoSettings, Partner, Settlement, SettlementLine

logger = logging.getLogger(__name__)

BASE_URL = "https://api.billingo.hu/v3"

PAYMENT_METHOD_MAP = {
    "cash": "cash",
    "card": "bankcard",
    "transfer": "wire_transfer",
}


async def get_or_create_settings(db: AsyncSession) -> BillingoSettings:
    row = (
        await db.execute(select(BillingoSettings).where(BillingoSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = BillingoSettings(id=1)
        db.add(row)
        await db.flush()
    return row


def _vat_label(percent: int) -> str:
    """Billingó ÁFA-kulcs címke a százalékból."""
    return {0: "0%", 5: "5%", 18: "18%", 27: "27%"}.get(percent, "27%")


async def _api(
    api_key: str, method: str, path: str, json_body: dict | None = None
) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.request(
            method,
            f"{BASE_URL}{path}",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json=json_body,
        )
        res.raise_for_status()
        return res.json() if res.content else {}


async def _find_or_create_billingo_partner(api_key: str, partner: Partner) -> int:
    """Partner keresése névre; ha nincs, létrehozás. Visszaadja a Billingó id-t."""
    data = await _api(api_key, "GET", f"/partners?query={httpx.QueryParams({'q': partner.name})['q']}")
    for item in data.get("data", []):
        if item.get("name", "").strip().lower() == partner.name.strip().lower():
            return int(item["id"])

    # Strukturált számlázási cím előnyben; visszaesés a székhelyre / egysorosra.
    zip_ = partner.billing_zip or partner.address_zip or "0000"
    city = partner.billing_city or partner.address_city or "-"
    street = " ".join(
        x for x in (
            partner.billing_street or partner.address_street,
            partner.billing_number or partner.address_number,
        ) if x
    ) or (partner.billing_address or partner.address or "-")
    body = {
        "name": partner.name,
        "address": {
            "country_code": "HU",
            "post_code": zip_,
            "city": city,
            "address": street,
        },
        "emails": [partner.contact_email] if partner.contact_email else [],
        "taxcode": partner.tax_number or "",
    }
    created = await _api(api_key, "POST", "/partners", body)
    return int(created["id"])


async def create_invoice_for_settlement(
    db: AsyncSession, settlement: Settlement, partner: Partner
) -> tuple[str, str]:
    """Billingó bizonylat létrehozása az elszámoláshoz.

    Visszatérés: (document_id, mode) — mode: 'proforma' (teszt) | 'invoice'.
    ValueError('billingo_not_configured'), ha nincs kulcs/blokk-azonosító.
    """
    settings = await get_or_create_settings(db)
    api_key = decrypt_pii(settings.api_key_encrypted)
    if not settings.enabled or not api_key or not settings.block_id:
        raise ValueError("billingo_not_configured")

    lines = (
        (
            await db.execute(
                select(SettlementLine).where(SettlementLine.settlement_id == settlement.id)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "name": f"{line.product_name} — fogyás ({line.consumed_qty:g} kg / {line.portions:.0f} adag)",
            "unit_price": line.price_per_portion,
            "unit_price_type": "net",
            "quantity": round(line.portions, 2),
            "unit": "adag",
            "vat": _vat_label(line.vat_percent),
        }
        for line in lines
        if line.portions > 0
    ]
    if not items:
        raise ValueError("billingo_no_items")

    billingo_partner_id = await _find_or_create_billingo_partner(api_key, partner)

    from datetime import date, timedelta

    today = date.today()
    doc_type = "proforma" if settings.test_mode else "invoice"
    body = {
        "partner_id": billingo_partner_id,
        "block_id": settings.block_id,
        "type": doc_type,
        "fulfillment_date": today.isoformat(),
        "due_date": (today + timedelta(days=8)).isoformat(),
        "payment_method": PAYMENT_METHOD_MAP.get(settlement.payment_method, "cash"),
        "language": "hu",
        "currency": "HUF",
        "electronic": False,
        "items": items,
    }
    created = await _api(api_key, "POST", "/documents", body)
    return str(created.get("id", "")), doc_type


async def test_connection(db: AsyncSession) -> dict:
    """Kapcsolat-teszt: a számlatömbök lekérése (nem hoz létre semmit)."""
    settings = await get_or_create_settings(db)
    api_key = decrypt_pii(settings.api_key_encrypted)
    if not api_key:
        raise ValueError("billingo_not_configured")
    data = await _api(api_key, "GET", "/document-blocks?type=invoice")
    blocks = [
        {"id": item.get("id"), "name": item.get("name")}
        for item in data.get("data", [])
    ]
    return {"ok": True, "blocks": blocks}
