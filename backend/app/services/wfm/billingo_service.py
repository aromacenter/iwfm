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
from datetime import date, timedelta

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
    "cod": "cash_on_delivery",
}


COMPANIES = {"xp": "X-Presso Coffee Kft.", "pc": "Premium Caffe Kft."}

# Adagáras (számlálós) kávé-tétel megnevezése a számlán — a szerződés bérleti
# díjként tartalmazza az adagra jutó kávé árát, a számlára EZ a szöveg megy.
PORTION_LINE_NAME = (
    "Bérleti díj (szerződés szerint tartalmazza az 1 adagra jutó kávé árát is)"
)
# A nem elektronikus bizonylatokra kerülő megjegyzés.
INVOICE_COMMENT = "A bizonylat aláírás és bélyegző nélkül is érvényes!"


def settlement_due_date(settlement: Settlement, partner: Partner) -> date:
    """A bizonylat fizetési határideje.

    Készpénz/kártya: minden dátum (teljesítés, kiállítás, határidő) a mai nap.
    Átutalás/utánvét: az elszámoláson megadott eseti napok → aktív szerződés →
    partner-beállítás → 8 nap erősorrend."""
    today = date.today()
    if settlement.payment_method in ("cash", "card"):
        return today
    days = getattr(settlement, "due_days", None)
    if not days or days <= 0:
        days = (
            partner.contract_payment_terms_days
            if (partner.contract_payment_terms_days or 0) > 0
            else partner.payment_terms_days if (partner.payment_terms_days or 0) > 0 else 8
        )
    return today + timedelta(days=days)


async def settlement_invoice_items(db: AsyncSession, settlement: Settlement) -> list[dict]:
    """A számla-tételek az elszámolás soraiból, Billingó item-formában.

    Adagáras kávé-tétel (bizományos termék, adagra számlázva) a számlán
    egységesen bérleti díjként szerepel; minden más tétel a nevén."""
    from app.models import Product

    lines = (
        (
            await db.execute(
                select(SettlementLine).where(SettlementLine.settlement_id == settlement.id)
            )
        )
        .scalars()
        .all()
    )
    pids = [line.product_id for line in lines if line.product_id is not None]
    consignment_ids: set = set()
    if pids:
        rows = (
            await db.execute(select(Product.id).where(Product.id.in_(pids), Product.is_consignment.is_(True)))
        ).scalars().all()
        consignment_ids = set(rows)
    items = []
    for line in lines:
        if line.portions <= 0:
            continue
        portion_based = (
            line.product_id in consignment_ids
            and not line.product_name.startswith("Készlethiány")
        )
        items.append({
            "name": PORTION_LINE_NAME if portion_based
            else f"{line.product_name} — fogyás ({line.consumed_qty:g} kg / {line.portions:.0f} adag)"
            if line.consumed_qty > 0 and line.product_id in consignment_ids
            else line.product_name,
            "unit_price": line.price_per_portion,
            "unit_price_type": "net",
            "quantity": round(line.portions, 2),
            "unit": "adag" if portion_based else ("adag" if line.product_id in consignment_ids else "db"),
            "vat": _vat_label(line.vat_percent),
        })
    return items


async def get_or_create_settings(db: AsyncSession) -> BillingoSettings:
    row = (
        await db.execute(select(BillingoSettings).where(BillingoSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = BillingoSettings(id=1)
        db.add(row)
        await db.flush()
    return row


def _account(settings: BillingoSettings, company: str | None) -> tuple[str | None, int | None, bool]:
    """A céghez tartozó Billingó-fiók: (api_kulcs, számlatömb, teszt_mód).
    'pc' → Premium Caffe fiók; minden más (xp / nincs megadva) → az 1. fiók."""
    if company == "pc":
        return decrypt_pii(settings.pc_api_key_encrypted), settings.pc_block_id, settings.pc_test_mode
    return decrypt_pii(settings.api_key_encrypted), settings.block_id, settings.test_mode


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
    """Partner keresése névre; ha nincs, létrehozás. Visszaadja a Billingó id-t.
    A számlán a hivatalos cégnév szerepel (company_name), ha meg van adva."""
    invoice_name = (partner.company_name or "").strip() or partner.name
    data = await _api(api_key, "GET", f"/partners?query={httpx.QueryParams({'q': invoice_name})['q']}")
    for item in data.get("data", []):
        if item.get("name", "").strip().lower() == invoice_name.strip().lower():
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
        "name": invoice_name,
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
) -> tuple[str, str, date]:
    """Billingó bizonylat létrehozása az elszámoláshoz.

    Visszatérés: (document_id, mode, due_date) — mode: 'proforma' (teszt) |
    'invoice'. A fizetési határidő az aktív szerződésből, annak híján a
    partner payment_terms_days értékéből jön (alapértelmezés: 8 nap).
    ValueError('billingo_not_configured'), ha nincs kulcs/blokk-azonosító.
    """
    settings = await get_or_create_settings(db)
    company = settlement.invoicing_company or partner.invoicing_company
    api_key, block_id, test_mode = _account(settings, company)
    if not settings.enabled or not api_key or not block_id:
        raise ValueError("billingo_not_configured")

    items = await settlement_invoice_items(db, settlement)
    if not items:
        raise ValueError("billingo_no_items")

    billingo_partner_id = await _find_or_create_billingo_partner(api_key, partner)

    today = date.today()
    # Készpénz/kártya: minden dátum a mai nap; átutalásnál eseti napok →
    # szerződés → partner → 8 nap erősorrend.
    due = settlement_due_date(settlement, partner)
    doc_type = "proforma" if test_mode else "invoice"
    body = {
        "partner_id": billingo_partner_id,
        "block_id": block_id,
        "type": doc_type,
        "fulfillment_date": today.isoformat(),
        "due_date": due.isoformat(),
        "payment_method": PAYMENT_METHOD_MAP.get(settlement.payment_method, "cash"),
        "language": "hu",
        "currency": "HUF",
        "electronic": False,
        "comment": INVOICE_COMMENT,
        "items": items,
    }
    created = await _api(api_key, "POST", "/documents", body)
    return str(created.get("id", "")), doc_type, due


async def create_maintenance_invoice(
    db: AsyncSession,
    partner: Partner,
    *,
    serial: str,
    asset_label: str,
    amount_net: float,
    vat_percent: int = 27,
) -> tuple[str, str, date]:
    """Karbantartási díj számlázása (KSZ-munkalap aláírása után, automatikus).

    Visszatérés: (document_id, mode, due_date). A cég a partner szerződött
    cége; a határidő a szerződés → partner → 8 nap erősorrend.
    ValueError('billingo_not_configured'), ha nincs kulcs/blokk.
    """
    settings = await get_or_create_settings(db)
    api_key, block_id, test_mode = _account(settings, partner.invoicing_company)
    if not settings.enabled or not api_key or not block_id:
        raise ValueError("billingo_not_configured")

    billingo_partner_id = await _find_or_create_billingo_partner(api_key, partner)
    today = date.today()
    terms_days = (
        partner.contract_payment_terms_days
        if (partner.contract_payment_terms_days or 0) > 0
        else partner.payment_terms_days if (partner.payment_terms_days or 0) > 0 else 8
    )
    due = today + timedelta(days=terms_days)
    doc_type = "proforma" if test_mode else "invoice"
    body = {
        "partner_id": billingo_partner_id,
        "block_id": block_id,
        "type": doc_type,
        "fulfillment_date": today.isoformat(),
        "due_date": due.isoformat(),
        "payment_method": "wire_transfer",
        "language": "hu",
        "currency": "HUF",
        "electronic": False,
        "items": [{
            "name": f"Karbantartási díj — {asset_label} ({serial})",
            "unit_price": amount_net,
            "unit_price_type": "net",
            "quantity": 1,
            "unit": "alkalom",
            "vat": _vat_label(vat_percent),
        }],
    }
    created = await _api(api_key, "POST", "/documents", body)
    return str(created.get("id", "")), doc_type, due


async def create_handover_invoice(
    db: AsyncSession,
    partner: Partner,
    *,
    serial: str,
    items: list[dict],
    payment_method: str,  # "cash" | "bankcard"
    vat_percent: int = 27,
) -> tuple[str, str]:
    """Szerviz-átadás számlázása a helyszínen: azonnali teljesítés/fizetés,
    készpénz vagy bankkártya. ``items``: [{name, amount_net}].
    Visszatérés: (document_id, mode).
    ValueError('billingo_not_configured'), ha nincs kulcs/blokk."""
    settings = await get_or_create_settings(db)
    api_key, block_id, test_mode = _account(settings, partner.invoicing_company)
    if not settings.enabled or not api_key or not block_id:
        raise ValueError("billingo_not_configured")

    billingo_partner_id = await _find_or_create_billingo_partner(api_key, partner)
    today = date.today()
    doc_type = "proforma" if test_mode else "invoice"
    body = {
        "partner_id": billingo_partner_id,
        "block_id": block_id,
        "type": doc_type,
        "fulfillment_date": today.isoformat(),
        "due_date": today.isoformat(),
        "payment_method": "bankcard" if payment_method == "card" else "cash",
        "language": "hu",
        "currency": "HUF",
        "electronic": False,
        "items": [
            {
                "name": f"{it['name']} ({serial})"[:255],
                "unit_price": it["amount_net"],
                "unit_price_type": "net",
                "quantity": 1,
                "unit": "db",
                "vat": _vat_label(vat_percent),
            }
            for it in items
        ],
    }
    created = await _api(api_key, "POST", "/documents", body)
    return str(created.get("id", "")), doc_type


async def download_document_pdf(
    db: AsyncSession, document_id: str, company: str | None = None
) -> bytes:
    """A Billingó-bizonylat számlaképe PDF-ben (GET /documents/{id}/download).
    ValueError('billingo_not_configured'), ha nincs API-kulcs."""
    settings = await get_or_create_settings(db)
    api_key, _block, _test = _account(settings, company)
    if not settings.enabled or not api_key:
        raise ValueError("billingo_not_configured")
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(
            f"{BASE_URL}/documents/{document_id}/download",
            headers={"X-API-KEY": api_key},
        )
        res.raise_for_status()
        return res.content


async def send_document_email(
    db: AsyncSession, document_id: str, emails: list[str], company: str | None = None
) -> None:
    """A bizonylat (újra)küldése emailben a Billingón keresztül
    (POST /documents/{id}/send). ValueError('billingo_not_configured')."""
    settings = await get_or_create_settings(db)
    api_key, _block, _test = _account(settings, company)
    if not settings.enabled or not api_key:
        raise ValueError("billingo_not_configured")
    await _api(api_key, "POST", f"/documents/{document_id}/send", {"emails": emails})


async def fetch_payment_status(
    db: AsyncSession, document_id: str, company: str | None = None
) -> str | None:
    """A Billingó-bizonylat fizetési státusza (pl. 'paid', 'no_payment',
    'partially_paid'). None, ha a válaszban nincs státusz. A ``company``
    (xp|pc) dönti el, melyik fiókkal kérdezünk.
    ValueError('billingo_not_configured'), ha nincs API-kulcs."""
    settings = await get_or_create_settings(db)
    api_key, _block, _test = _account(settings, company)
    if not settings.enabled or not api_key:
        raise ValueError("billingo_not_configured")
    data = await _api(api_key, "GET", f"/documents/{document_id}")
    status = data.get("payment_status")
    return str(status) if status else None


async def test_connection(db: AsyncSession, company: str | None = None) -> dict:
    """Kapcsolat-teszt: a számlatömbök lekérése (nem hoz létre semmit)."""
    settings = await get_or_create_settings(db)
    api_key, _block, _test = _account(settings, company)
    if not api_key:
        raise ValueError("billingo_not_configured")
    data = await _api(api_key, "GET", "/document-blocks?type=invoice")
    blocks = [
        {"id": item.get("id"), "name": item.get("name")}
        for item in data.get("data", [])
    ]
    return {"ok": True, "blocks": blocks}
