"""CashBook (SBA Group) könyvelési feladás — Document pool API v3.

Az elszámolás-bizonylatból NAV OSA 3.0 formájú számla-XML készül (a CashBook
kiegyenlítés-kiterjesztésével: invoicePayments), és a bizonylat-PDF-fel együtt
form-POST-ban megy a document pool-ba. Kifizetéskor ugyanaz az XML megy újra a
kiegyenlítés-adatokkal (a PDF mező ilyenkor kötelező, de lehet placeholder).

API: POST {base}/api/v3/document/pool/osa (Authorization: Bearer <kulcs>,
X-Request-ID: Iwfm, form-data: xml, pdf[BASE64]); napló: GET /document/pool/log;
kulcs-teszt: GET /oauth2/tokenrelation?taxid=<törzsszám>.
Sandbox: https://cashbook.io — éles: https://cashbook.hu (test_mode dönt).
"""

from __future__ import annotations

import base64
import logging
from xml.sax.saxutils import escape

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_pii
from app.models import CashbookSettings, Partner, Settlement, SettlementLine

logger = logging.getLogger(__name__)

SOFTWARE_ID = "Iwfm"

# OSA paymentMethod értékek; utánvét a sémán kívüli → OTHER + C00013 kiegészítés.
OSA_PAYMENT = {"cash": "CASH", "card": "CARD", "transfer": "TRANSFER", "cod": "OTHER"}


def _base_url(settings: CashbookSettings) -> str:
    return "https://cashbook.io" if settings.test_mode else "https://cashbook.hu"


async def get_or_create_settings(db: AsyncSession) -> CashbookSettings:
    row = (
        await db.execute(select(CashbookSettings).where(CashbookSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = CashbookSettings(id=1)
        db.add(row)
        await db.flush()
    return row


def _tax_parts(tax_number: str | None) -> tuple[str, str, str] | None:
    """'12345678-2-42' → (törzsszám, áfakód, megyekód); csonka adószámot is tűr."""
    raw = (tax_number or "").replace(" ", "")
    if not raw:
        return None
    parts = raw.split("-")
    core = parts[0][:8]
    if len(core) != 8 or not core.isdigit():
        return None
    vat = parts[1][:1] if len(parts) > 1 and parts[1] else "2"
    county = parts[2][:2] if len(parts) > 2 and parts[2] else "42"
    return core, vat, county


def _fmt(x: float) -> str:
    return f"{round(x, 2):.2f}"


def _supplier_xml(settings: CashbookSettings) -> str:
    tax = _tax_parts(settings.supplier_tax_number)
    if tax is None:
        raise ValueError("cashbook_not_configured")
    core, vat, county = tax
    return (
        "<supplierInfo>"
        "<supplierTaxNumber>"
        f"<base:taxpayerId>{core}</base:taxpayerId>"
        f"<base:vatCode>{vat}</base:vatCode>"
        f"<base:countyCode>{county}</base:countyCode>"
        "</supplierTaxNumber>"
        f"<supplierName>{escape(settings.supplier_name or '')}</supplierName>"
        "<supplierAddress><base:simpleAddress>"
        "<base:countryCode>HU</base:countryCode>"
        f"<base:postalCode>{escape(settings.supplier_zip or '0000')}</base:postalCode>"
        f"<base:city>{escape(settings.supplier_city or '-')}</base:city>"
        f"<base:additionalAddressDetail>{escape(settings.supplier_street or '-')}</base:additionalAddressDetail>"
        "</base:simpleAddress></supplierAddress>"
        "</supplierInfo>"
    )


def _customer_xml(partner: Partner) -> str:
    """Vevő-blokk: adószámos partner DOMESTIC, egyébként PRIVATE_PERSON —
    a magánszemély nevét és címét is át KELL adni a CashBooknak."""
    name = (partner.company_name or "").strip() or partner.name
    zip_ = partner.billing_zip or partner.address_zip or "0000"
    city = partner.billing_city or partner.address_city or "-"
    street = " ".join(
        x for x in (
            partner.billing_street or partner.address_street,
            partner.billing_number or partner.address_number,
        ) if x
    ) or (partner.billing_address or partner.address or "-")
    tax = _tax_parts(partner.tax_number)
    if tax is not None:
        core, vat, county = tax
        status_block = (
            "<customerVatStatus>DOMESTIC</customerVatStatus>"
            "<customerVatData><customerTaxNumber>"
            f"<base:taxpayerId>{core}</base:taxpayerId>"
            f"<base:vatCode>{vat}</base:vatCode>"
            f"<base:countyCode>{county}</base:countyCode>"
            "</customerTaxNumber></customerVatData>"
        )
    else:
        status_block = "<customerVatStatus>PRIVATE_PERSON</customerVatStatus>"
    return (
        "<customerInfo>"
        + status_block
        + f"<customerName>{escape(name)}</customerName>"
        "<customerAddress><base:simpleAddress>"
        "<base:countryCode>HU</base:countryCode>"
        f"<base:postalCode>{escape(zip_)}</base:postalCode>"
        f"<base:city>{escape(city)}</base:city>"
        f"<base:additionalAddressDetail>{escape(street)}</base:additionalAddressDetail>"
        "</base:simpleAddress></customerAddress>"
        "</customerInfo>"
    )


def _line_xml(idx: int, line: SettlementLine) -> str:
    net = round(line.amount_net, 2)
    vat_amount = round(net * line.vat_percent / 100.0, 2)
    gross = round(net + vat_amount, 2)
    qty = round(line.portions, 6)
    unit_price = round(line.price_per_portion, 4)
    return (
        "<line>"
        f"<lineNumber>{idx}</lineNumber>"
        "<lineExpressionIndicator>true</lineExpressionIndicator>"
        f"<lineDescription>{escape(line.product_name[:255])}</lineDescription>"
        f"<quantity>{qty:.6f}</quantity>"
        "<unitOfMeasure>PIECE</unitOfMeasure>"
        "<unitOfMeasureOwn>adag</unitOfMeasureOwn>"
        f"<unitPrice>{unit_price:.4f}</unitPrice>"
        f"<unitPriceHUF>{unit_price:.4f}</unitPriceHUF>"
        "<lineAmountsNormal>"
        "<lineNetAmountData>"
        f"<lineNetAmount>{_fmt(net)}</lineNetAmount>"
        f"<lineNetAmountHUF>{_fmt(net)}</lineNetAmountHUF>"
        "</lineNetAmountData>"
        f"<lineVatRate><vatPercentage>{line.vat_percent / 100:.4f}</vatPercentage></lineVatRate>"
        "<lineVatData>"
        f"<lineVatAmount>{_fmt(vat_amount)}</lineVatAmount>"
        f"<lineVatAmountHUF>{_fmt(vat_amount)}</lineVatAmountHUF>"
        "</lineVatData>"
        "<lineGrossAmountData>"
        f"<lineGrossAmountNormal>{_fmt(gross)}</lineGrossAmountNormal>"
        f"<lineGrossAmountNormalHUF>{_fmt(gross)}</lineGrossAmountNormalHUF>"
        "</lineGrossAmountData>"
        "</lineAmountsNormal>"
        "</line>"
    )


def _payments_xml(settlement: Settlement, settings: CashbookSettings) -> str:
    """Kiegyenlítés-blokk (CashBook OSA-kiterjesztés) — csak fizetett számlára."""
    if settlement.payment_status != "paid" or settlement.paid_at is None:
        return ""
    cash_like = settlement.payment_method in ("cash", "card")
    location = "P" if settlement.payment_method == "cash" else "B"
    ledger = settings.ledger_cash if location == "P" else settings.ledger_bank
    paid_amount = (
        settlement.paid_amount
        if settlement.paid_amount is not None and settlement.paid_amount > 0 and not cash_like
        else settlement.total_gross
    )
    return (
        "<invoicePayments><payment>"
        f"<locationCode>{location}</locationCode>"
        f"<locationLedger>{escape(ledger or '381')}</locationLedger>"
        f"<paymentMethod>{OSA_PAYMENT.get(settlement.payment_method, 'OTHER')}</paymentMethod>"
        f"<paymentDate>{settlement.paid_at.date().isoformat()}</paymentDate>"
        f"<amount>{_fmt(paid_amount)}</amount>"
        "<currencyCode>HUF</currencyCode>"
        "<exchangeRate>1</exchangeRate>"
        f"<warrantNumber>{escape(settlement.billingo_document_id or '-')}</warrantNumber>"
        "<paymentComment>Elszámolás kiegyenlítése</paymentComment>"
        "</payment></invoicePayments>"
    )


def build_invoice_xml(
    settlement: Settlement,
    lines: list[SettlementLine],
    partner: Partner,
    settings: CashbookSettings,
    *,
    invoice_number: str,
) -> str:
    """OSA 3.0 InvoiceData a CashBook-kiterjesztésekkel — az elszámolásból."""
    billable = [ln for ln in lines if ln.portions > 0 or ln.amount_net != 0]
    if not billable:
        raise ValueError("cashbook_no_items")
    issue = settlement.created_at.date().isoformat()
    method = OSA_PAYMENT.get(settlement.payment_method, "OTHER")
    extra_method = (
        "<additionalInvoiceData>"
        "<dataName>C00013_ADDITIONAL_PAYMENT_METHOD</dataName>"
        "<dataDescription>Additional payment methods</dataDescription>"
        "<dataValue>CASHONDELIVERY</dataValue>"
        "</additionalInvoiceData>"
        if settlement.payment_method == "cod"
        else ""
    )
    # ÁFA-kulcsonkénti összesítő
    by_vat: dict[int, list[float]] = {}
    for ln in billable:
        net = round(ln.amount_net, 2)
        vat_amount = round(net * ln.vat_percent / 100.0, 2)
        agg = by_vat.setdefault(ln.vat_percent, [0.0, 0.0])
        agg[0] += net
        agg[1] += vat_amount
    total_net = sum(a[0] for a in by_vat.values())
    total_vat = sum(a[1] for a in by_vat.values())
    summary_rates = "".join(
        "<summaryByVatRate>"
        f"<vatRate><vatPercentage>{pct / 100:.4f}</vatPercentage></vatRate>"
        "<vatRateNetData>"
        f"<vatRateNetAmount>{_fmt(net)}</vatRateNetAmount>"
        f"<vatRateNetAmountHUF>{_fmt(net)}</vatRateNetAmountHUF>"
        "</vatRateNetData>"
        "<vatRateVatData>"
        f"<vatRateVatAmount>{_fmt(vat)}</vatRateVatAmount>"
        f"<vatRateVatAmountHUF>{_fmt(vat)}</vatRateVatAmountHUF>"
        "</vatRateVatData>"
        "<vatRateGrossData>"
        f"<vatRateGrossAmount>{_fmt(net + vat)}</vatRateGrossAmount>"
        f"<vatRateGrossAmountHUF>{_fmt(net + vat)}</vatRateGrossAmountHUF>"
        "</vatRateGrossData>"
        "</summaryByVatRate>"
        for pct, (net, vat) in sorted(by_vat.items())
    )
    line_xml = "".join(_line_xml(i + 1, ln) for i, ln in enumerate(billable))
    due = settlement.due_date.isoformat() if settlement.due_date else issue
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<InvoiceData xmlns="http://schemas.nav.gov.hu/OSA/3.0/data" '
        'xmlns:base="http://schemas.nav.gov.hu/OSA/3.0/base">'
        f"<invoiceNumber>{escape(invoice_number)}</invoiceNumber>"
        f"<invoiceIssueDate>{issue}</invoiceIssueDate>"
        "<completenessIndicator>false</completenessIndicator>"
        "<invoiceMain><invoice>"
        "<invoiceHead>"
        + _supplier_xml(settings)
        + _customer_xml(partner)
        + "<invoiceDetail>"
        "<invoiceCategory>NORMAL</invoiceCategory>"
        f"<invoiceDeliveryDate>{issue}</invoiceDeliveryDate>"
        "<smallBusinessIndicator>false</smallBusinessIndicator>"
        "<currencyCode>HUF</currencyCode>"
        "<exchangeRate>1</exchangeRate>"
        f"<paymentMethod>{method}</paymentMethod>"
        f"<paymentDate>{due}</paymentDate>"
        "<invoiceAppearance>PAPER</invoiceAppearance>"
        + extra_method
        + "</invoiceDetail>"
        "</invoiceHead>"
        "<invoiceLines>"
        "<mergedItemIndicator>false</mergedItemIndicator>"
        + line_xml
        + "</invoiceLines>"
        "<invoiceSummary>"
        "<summaryNormal>"
        + summary_rates
        + f"<invoiceNetAmount>{_fmt(total_net)}</invoiceNetAmount>"
        f"<invoiceNetAmountHUF>{_fmt(total_net)}</invoiceNetAmountHUF>"
        f"<invoiceVatAmount>{_fmt(total_vat)}</invoiceVatAmount>"
        f"<invoiceVatAmountHUF>{_fmt(total_vat)}</invoiceVatAmountHUF>"
        "</summaryNormal>"
        "<summaryGrossData>"
        f"<invoiceGrossAmount>{_fmt(total_net + total_vat)}</invoiceGrossAmount>"
        f"<invoiceGrossAmountHUF>{_fmt(total_net + total_vat)}</invoiceGrossAmountHUF>"
        "</summaryGrossData>"
        "</invoiceSummary>"
        + _payments_xml(settlement, settings)
        + "</invoice></invoiceMain></InvoiceData>"
    )


async def _post_pool(settings: CashbookSettings, xml: str, pdf_field: str) -> str:
    """Form-POST a document pool-ba; visszaadja a beküldés hash-ét."""
    api_key = decrypt_pii(settings.api_key_encrypted)
    if not api_key:
        raise ValueError("cashbook_not_configured")
    async with httpx.AsyncClient(timeout=45) as client:
        res = await client.post(
            f"{_base_url(settings)}/api/v3/document/pool/osa",
            headers={"Authorization": f"Bearer {api_key}", "X-Request-ID": SOFTWARE_ID},
            data={"xml": xml, "pdf": pdf_field},
        )
        res.raise_for_status()
        data = res.json()
    return str(data.get("hash", ""))


async def send_settlement(db: AsyncSession, settlement: Settlement) -> tuple[str, str]:
    """Az elszámolás feladása a CashBooknak: OSA-XML + bizonylat-PDF.

    Fizetett számlánál a kiegyenlítés-blokk is megy; újraküldésnél (már van
    hash) a PDF placeholder — a CashBook így is elfogadja.
    Visszatérés: (hash, status: sent|paid_sent).
    ValueError('cashbook_not_configured') kulcs/szállító-adat híján."""
    settings = await get_or_create_settings(db)
    if not settings.enabled or not settings.api_key_encrypted:
        raise ValueError("cashbook_not_configured")
    partner = (
        await db.execute(select(Partner).where(Partner.id == settlement.partner_id))
    ).scalar_one_or_none()
    if partner is None:
        raise ValueError("cashbook_not_configured")
    lines = (
        (
            await db.execute(
                select(SettlementLine).where(SettlementLine.settlement_id == settlement.id)
            )
        )
        .scalars()
        .all()
    )
    from app.api.consignment import _build_settlement_pdf, _receipt_no

    invoice_number = settlement.billingo_document_id or _receipt_no(settlement)
    xml = build_invoice_xml(
        settlement, list(lines), partner, settings, invoice_number=invoice_number
    )
    if settlement.cashbook_hash:
        pdf_field = "PDF"  # kiegyenlítés-újraküldés: a PDF már fent van
    else:
        pdf, _no = await _build_settlement_pdf(db, settlement)
        pdf_field = base64.b64encode(pdf).decode()
    pool_hash = await _post_pool(settings, xml, pdf_field)
    status = "paid_sent" if settlement.payment_status == "paid" else "sent"
    return pool_hash, status


async def fetch_log(
    db: AsyncSession, *, pool_hash: str | None = None,
    log_date: str | None = None, failed: bool = False,
) -> list[dict]:
    """A document pool feldolgozási naplója (státusz-kódokkal)."""
    settings = await get_or_create_settings(db)
    api_key = decrypt_pii(settings.api_key_encrypted)
    if not api_key:
        raise ValueError("cashbook_not_configured")
    params: dict = {}
    if pool_hash:
        params["hash"] = pool_hash
    if log_date:
        params["date"] = log_date
    if failed:
        params["failed"] = ""
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(
            f"{_base_url(settings)}/api/v3/document/pool/detailedlog",
            headers={"Authorization": f"Bearer {api_key}", "X-Request-ID": SOFTWARE_ID},
            params=params,
        )
        res.raise_for_status()
        data = res.json()
    return list(data.get("log", []))


async def test_connection(db: AsyncSession) -> dict:
    """Kulcs-teszt: a szállító törzsszámával lekérdezzük a fiók-viszonyt."""
    settings = await get_or_create_settings(db)
    api_key = decrypt_pii(settings.api_key_encrypted)
    tax = _tax_parts(settings.supplier_tax_number)
    if not api_key or tax is None:
        raise ValueError("cashbook_not_configured")
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(
            f"{_base_url(settings)}/api/v3/oauth2/tokenrelation",
            headers={"Authorization": f"Bearer {api_key}", "X-Request-ID": SOFTWARE_ID},
            params={"taxid": tax[0]},
        )
        res.raise_for_status()
        data = res.json()
    return {"ok": True, "response": str(data.get("response", ""))}
