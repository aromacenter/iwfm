"""Számlázz.hu Számla Agent integráció — a Billingó alternatívája.

Az Agent-kulcs Fernet-titkosítva a billingo_settings sorban (közös számlázó-
beállítás, provider='szamlazz'). Teszt-módban díjbekérő készül — az NEM kerül
a NAV-hoz; éles módban számla. A partner adatai a Partner törzsből mennek.

API: https://www.szamlazz.hu/szamla/ — multipart POST, a kérés egy XML fájl
(action-xmlagentxmlfile), a válasz állapota a szlahu_* fejlécekben jön
(valaszVerzio=1). Hiba esetén szlahu_error_code + szlahu_error fejléc.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from xml.sax.saxutils import escape

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_pii
from app.models import BillingoSettings, Partner, Settlement, SettlementLine

logger = logging.getLogger(__name__)

AGENT_URL = "https://www.szamlazz.hu/szamla/"
XMLNS = "http://www.szamlazz.hu/xmlszamla"

# Számlázz.hu a fizetési módot szabad szöveggel várja (magyarul).
PAYMENT_METHOD_MAP = {
    "cash": "Készpénz",
    "card": "Bankkártya",
    "transfer": "Átutalás",
    "cod": "Utánvét",
}


async def _get_settings(db: AsyncSession) -> BillingoSettings:
    row = (
        await db.execute(select(BillingoSettings).where(BillingoSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = BillingoSettings(id=1)
        db.add(row)
        await db.flush()
    return row


def _account(settings: BillingoSettings, company: str | None) -> tuple[str | None, str | None, bool]:
    """A céghez tartozó Számlázz.hu fiók: (agent_kulcs, előtag, teszt_mód).
    'pc' → Premium Caffe fiók; minden más (xp / nincs megadva) → az 1. fiók."""
    if company == "pc":
        return (
            decrypt_pii(settings.pc_szamlazz_agent_key_encrypted),
            settings.pc_szamlazz_prefix,
            settings.pc_test_mode,
        )
    return (
        decrypt_pii(settings.szamlazz_agent_key_encrypted),
        settings.szamlazz_prefix,
        settings.test_mode,
    )


def _vevo_xml(partner: Partner) -> str:
    """A vevő-blokk a Partner törzsből — a számlán a hivatalos cégnév megy."""
    invoice_name = (partner.company_name or "").strip() or partner.name
    zip_ = partner.billing_zip or partner.address_zip or "0000"
    city = partner.billing_city or partner.address_city or "-"
    street = " ".join(
        x for x in (
            partner.billing_street or partner.address_street,
            partner.billing_number or partner.address_number,
        ) if x
    ) or (partner.billing_address or partner.address or "-")
    email = partner.contact_email or ""
    tax = partner.tax_number or ""
    return (
        "<vevo>"
        f"<nev>{escape(invoice_name)}</nev>"
        f"<irsz>{escape(zip_)}</irsz>"
        f"<telepules>{escape(city)}</telepules>"
        f"<cim>{escape(street)}</cim>"
        + (f"<email>{escape(email)}</email>" if email else "")
        + "<sendEmail>false</sendEmail>"
        + (f"<adoszam>{escape(tax)}</adoszam>" if tax else "")
        + "</vevo>"
    )


def _tetel_xml(name: str, qty: float, unit: str, unit_price_net: float, vat_percent: int) -> str:
    """Egy tétel-sor — a Számlázz.hu kéri a kiszámolt nettó/ÁFA/bruttó értéket is."""
    net = round(qty * unit_price_net, 2)
    vat = round(net * vat_percent / 100.0, 2)
    return (
        "<tetel>"
        f"<megnevezes>{escape(name[:255])}</megnevezes>"
        f"<mennyiseg>{qty:g}</mennyiseg>"
        f"<mennyisegiEgyseg>{escape(unit)}</mennyisegiEgyseg>"
        f"<nettoEgysegar>{unit_price_net:g}</nettoEgysegar>"
        f"<afakulcs>{vat_percent}</afakulcs>"
        f"<nettoErtek>{net:g}</nettoErtek>"
        f"<afaErtek>{vat:g}</afaErtek>"
        f"<bruttoErtek>{round(net + vat, 2):g}</bruttoErtek>"
        "</tetel>"
    )


def _invoice_xml(
    agent_key: str,
    prefix: str | None,
    *,
    test_mode: bool,
    payment_method: str,
    due: date,
    vevo: str,
    tetelek: list[str],
) -> str:
    today = date.today().isoformat()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<xmlszamla xmlns="{XMLNS}">'
        "<beallitasok>"
        f"<szamlaagentkulcs>{escape(agent_key)}</szamlaagentkulcs>"
        "<eszamla>false</eszamla>"
        "<szamlaLetoltes>false</szamlaLetoltes>"
        "<valaszVerzio>1</valaszVerzio>"
        "</beallitasok>"
        "<fejlec>"
        f"<keltDatum>{today}</keltDatum>"
        f"<teljesitesDatum>{today}</teljesitesDatum>"
        f"<fizetesiHataridoDatum>{due.isoformat()}</fizetesiHataridoDatum>"
        f"<fizmod>{PAYMENT_METHOD_MAP.get(payment_method, 'Készpénz')}</fizmod>"
        "<penznem>HUF</penznem>"
        "<szamlaNyelve>hu</szamlaNyelve>"
        "<megjegyzes></megjegyzes>"
        + (f"<szamlaszamElotag>{escape(prefix)}</szamlaszamElotag>" if prefix else "")
        + f"<dijbekero>{'true' if test_mode else 'false'}</dijbekero>"
        "</fejlec>"
        "<elado></elado>"
        f"{vevo}"
        f"<tetelek>{''.join(tetelek)}</tetelek>"
        "</xmlszamla>"
    )


async def _agent_call(field_name: str, xml: str) -> httpx.Headers:
    """Multipart POST az Agent felé; hibafejléc esetén ValueError a szöveggel."""
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        res = await client.post(
            AGENT_URL,
            files={field_name: ("invoice.xml", xml.encode("utf-8"), "text/xml")},
        )
        res.raise_for_status()
    err_code = res.headers.get("szlahu_error_code")
    if err_code:
        err = res.headers.get("szlahu_error", "")
        logger.warning("Szamlazz.hu agent hiba %s: %s", err_code, err)
        raise ValueError(f"szamlazz_error:{err_code}:{err[:200]}")
    return res.headers


def _terms_days(partner: Partner) -> int:
    return (
        partner.contract_payment_terms_days
        if (partner.contract_payment_terms_days or 0) > 0
        else partner.payment_terms_days if (partner.payment_terms_days or 0) > 0 else 8
    )


async def create_invoice_for_settlement(
    db: AsyncSession, settlement: Settlement, partner: Partner
) -> tuple[str, str, date]:
    """Számlázz.hu bizonylat az elszámoláshoz — a Billingó-hívás tükre.

    Visszatérés: (számlaszám, mode, due_date) — mode: 'proforma' (díjbekérő,
    teszt-mód) | 'invoice'. ValueError('billingo_not_configured'), ha nincs
    agent-kulcs (a hívók közös hibakódját tartjuk)."""
    settings = await _get_settings(db)
    company = settlement.invoicing_company or partner.invoicing_company
    agent_key, prefix, test_mode = _account(settings, company)
    if not settings.enabled or not agent_key:
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
    tetelek = [
        _tetel_xml(
            f"{line.product_name} — fogyás ({line.consumed_qty:g} kg / {line.portions:.0f} adag)",
            round(line.portions, 2), "adag", line.price_per_portion, line.vat_percent,
        )
        for line in lines
        if line.portions > 0
    ]
    if not tetelek:
        raise ValueError("billingo_no_items")

    due = date.today() + timedelta(days=_terms_days(partner))
    xml = _invoice_xml(
        agent_key, prefix, test_mode=test_mode,
        payment_method=settlement.payment_method, due=due,
        vevo=_vevo_xml(partner), tetelek=tetelek,
    )
    headers = await _agent_call("action-xmlagentxmlfile", xml)
    doc = headers.get("szlahu_szamlaszam", "")
    return doc, ("proforma" if test_mode else "invoice"), due


async def create_maintenance_invoice(
    db: AsyncSession,
    partner: Partner,
    *,
    serial: str,
    asset_label: str,
    amount_net: float,
    vat_percent: int = 27,
) -> tuple[str, str, date]:
    """Karbantartási díj számlázása Számlázz.hu-val (KSZ-munkalap után)."""
    settings = await _get_settings(db)
    agent_key, prefix, test_mode = _account(settings, partner.invoicing_company)
    if not settings.enabled or not agent_key:
        raise ValueError("billingo_not_configured")
    due = date.today() + timedelta(days=_terms_days(partner))
    xml = _invoice_xml(
        agent_key, prefix, test_mode=test_mode,
        payment_method="transfer", due=due,
        vevo=_vevo_xml(partner),
        tetelek=[_tetel_xml(
            f"Karbantartási díj — {asset_label} ({serial})",
            1, "alkalom", amount_net, vat_percent,
        )],
    )
    headers = await _agent_call("action-xmlagentxmlfile", xml)
    return (
        headers.get("szlahu_szamlaszam", ""),
        "proforma" if test_mode else "invoice",
        due,
    )


async def create_handover_invoice(
    db: AsyncSession,
    partner: Partner,
    *,
    serial: str,
    items: list[dict],
    payment_method: str,  # "cash" | "card"
    vat_percent: int = 27,
) -> tuple[str, str]:
    """Szerviz-átadás számlázása a helyszínen (azonnali teljesítés/fizetés)."""
    settings = await _get_settings(db)
    agent_key, prefix, test_mode = _account(settings, partner.invoicing_company)
    if not settings.enabled or not agent_key:
        raise ValueError("billingo_not_configured")
    today = date.today()
    xml = _invoice_xml(
        agent_key, prefix, test_mode=test_mode,
        payment_method=payment_method, due=today,
        vevo=_vevo_xml(partner),
        tetelek=[
            _tetel_xml(f"{it['name']} ({serial})", 1, "db", it["amount_net"], vat_percent)
            for it in items
        ],
    )
    headers = await _agent_call("action-xmlagentxmlfile", xml)
    return headers.get("szlahu_szamlaszam", ""), ("proforma" if test_mode else "invoice")


async def test_connection(db: AsyncSession, company: str | None = None) -> dict:
    """Kapcsolat-teszt: adózó-lekérdezés a NAV felől (nem hoz létre semmit) —
    rossz agent-kulcsnál a Számlázz.hu hibafejlécet ad, azt továbbdobjuk."""
    settings = await _get_settings(db)
    agent_key, _prefix, _test = _account(settings, company)
    if not agent_key:
        raise ValueError("billingo_not_configured")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xmltaxpayer xmlns="http://www.szamlazz.hu/xmltaxpayer">'
        "<beallitasok>"
        f"<szamlaagentkulcs>{escape(agent_key)}</szamlaagentkulcs>"
        "</beallitasok>"
        # A KBOSS.hu (a Számlázz.hu üzemeltetője) törzsszáma — csak a kulcs
        # érvényességét ellenőrizzük vele, adatot nem tárolunk.
        "<torzsszam>13421739</torzsszam>"
        "</xmltaxpayer>"
    )
    await _agent_call("action-szamla_agent_taxpayer", xml)
    return {"ok": True, "blocks": []}
