"""Fix bérleti díjas automata számlázás (1b2a4153 / 8e843400).

A fixed_rental szerződés a billing_day napján emberi beavatkozás nélkül
elszámolást készít (gépenként egy tétel: "Kávéfőző gép / daráló bérleti díj",
egység: hónap, ár: Asset.rent_fee) és azonnal számlát állít ki. Azonos
adószámú partnerek (üzemelési címek) gépei összevonhatók egy számlára
(rental_merge_company) — a tétel-megjegyzésben a cím nevével.

A 30+ napja lejárt tartozásnál a +10% késedelmi felár AUTOMATIKUS (itt nincs
pipa a kikapcsolására — a kézi elszámolásnál van).

A hónap-váltás védelme: rental_last_billed — egy hónapban legfeljebb egyszer
fut le szerződésenként; kimaradt nap (szerver-leállás) esetén a következő
körben pótolja, ha a hónap még tart és a nap már elmúlt.
"""

from __future__ import annotations

import calendar
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Asset,
    AuditEvent,
    Partner,
    PartnerContract,
    Settlement,
    SettlementLine,
)

logger = logging.getLogger(__name__)

RENTAL_LINE_NAME = "Kávéfőző gép / daráló bérleti díj"
LATE_FEE_PCT = 10.0


def _money(v: float) -> float:
    return float(round(v or 0.0))


def _effective_billing_day(billing_day: int, today: date) -> int:
    """A hónap utolsó napjára szorítva (pl. 31-e februárban = 28/29)."""
    last = calendar.monthrange(today.year, today.month)[1]
    return min(billing_day, last)


async def _overdue_ref(db: AsyncSession, partner_id: uuid.UUID):
    """A legrégebbi 30+ napja lejárt, rendezetlen elszámolás (késedelmi felár
    hivatkozásához) — a kézi elszámolás szabályával azonos."""
    cutoff_30 = date.today() - timedelta(days=30)
    for old_s in (
        await db.execute(
            select(Settlement)
            .where(Settlement.partner_id == partner_id)
            .order_by(Settlement.created_at)
        )
    ).scalars().all():
        paid = old_s.paid_amount if old_s.paid_amount is not None else old_s.total_gross
        if old_s.total_gross - paid <= 0.5:
            continue
        ref_day = old_s.due_date or old_s.created_at.date()
        if ref_day <= cutoff_30:
            return old_s, ref_day
    return None


async def run_fixed_rental_billing(db: AsyncSession) -> int:
    """Esedékes fix bérleti szerződések számlázása. Visszaadja, hány
    elszámolás készült (a háttérhurok hívja, best-effort)."""
    today = date.today()
    contracts = (
        await db.execute(
            select(PartnerContract).where(
                PartnerContract.fixed_rental.is_(True),
                PartnerContract.billing_day.is_not(None),
                PartnerContract.valid_from <= today,
                (PartnerContract.valid_to.is_(None))
                | (PartnerContract.valid_to >= today),
            )
        )
    ).scalars().all()
    created = 0
    for c in contracts:
        try:
            if await _bill_contract(db, c, today):
                created += 1
        except Exception:
            logger.exception("fixed rental billing failed for contract %s", c.id)
            await db.rollback()
    return created


async def _bill_contract(db: AsyncSession, c: PartnerContract, today: date) -> bool:
    eff_day = _effective_billing_day(int(c.billing_day or 1), today)
    if today.day < eff_day:
        return False  # még nem esedékes ebben a hónapban
    if c.rental_last_billed and (
        c.rental_last_billed.year == today.year
        and c.rental_last_billed.month == today.month
    ):
        return False  # ebben a hónapban már lefutott

    partner = (
        await db.execute(select(Partner).where(Partner.id == c.partner_id))
    ).scalar_one_or_none()
    if partner is None or not partner.is_active:
        return False

    # Érintett partnerek: összevonásnál az azonos adószámú (üzemelési cím)
    # partnerek is — kivéve, akinek SAJÁT fix bérleti szerződése él (azt a
    # sajátja számlázza, nem duplázunk).
    partners = [partner]
    if c.rental_merge_company and (partner.tax_number or "").strip():
        siblings = (
            await db.execute(
                select(Partner).where(
                    Partner.tax_number == partner.tax_number,
                    Partner.id != partner.id,
                    Partner.is_active.is_(True),
                )
            )
        ).scalars().all()
        for sib in siblings:
            own = (
                await db.execute(
                    select(PartnerContract.id).where(
                        PartnerContract.partner_id == sib.id,
                        PartnerContract.fixed_rental.is_(True),
                        PartnerContract.valid_from <= today,
                        (PartnerContract.valid_to.is_(None))
                        | (PartnerContract.valid_to >= today),
                    ).limit(1)
                )
            ).scalars().first()
            if own is None:
                partners.append(sib)

    assets: list[tuple[Asset, Partner]] = []
    for p in partners:
        rows = (
            await db.execute(
                select(Asset).where(
                    Asset.partner_id == p.id,
                    Asset.status == "deployed",
                    Asset.customer_owned.is_(False),
                )
                .order_by(Asset.barcode)
            )
        ).scalars().all()
        assets.extend((a, p) for a in rows if (a.rent_fee or 0) > 0)
    if not assets:
        logger.info("fixed rental: nincs díjas gép (%s) — kihagyva", partner.name)
        c.rental_last_billed = today  # ne próbálkozzon percenként egész hónapban
        await db.commit()
        return False

    month_label = f"{today.year}. {today.month:02d}. hó"
    settlement = Settlement(
        partner_id=partner.id,
        settled_by_user_id=None,
        settled_by_name="Automata bérleti számlázás",
        invoicing_company=partner.invoicing_company,
        payment_method=c.payment_method or "transfer",
        due_days=c.payment_terms_days,
        note=f"Fix bérleti díj — {month_label}",
    )
    db.add(settlement)
    await db.flush()

    total_net = 0.0
    for a, p in assets:
        site = f" ({p.name})" if (c.rental_merge_company and len(partners) > 1) else ""
        brand = " ".join(x for x in (a.manufacturer, a.name) if x)
        line_name = f"{RENTAL_LINE_NAME} — {brand}{site} — {month_label}"[:256]
        db.add(SettlementLine(
            settlement_id=settlement.id,
            product_id=None,
            product_name=line_name,
            previous_qty=0.0, physical_qty=0.0, consumed_qty=0.0,
            portions=1.0,  # 1 hónap
            price_per_portion=float(a.rent_fee or 0),
            vat_percent=27,
            amount_net=_money(a.rent_fee or 0),
        ))
        total_net += float(a.rent_fee or 0)

    # Automata késedelmi felár (emberi beavatkozás nélkül — 8e843400)
    overdue = await _overdue_ref(db, partner.id)
    if overdue is not None:
        factor = 1 + LATE_FEE_PCT / 100.0
        await db.flush()
        for line in (
            await db.execute(
                select(SettlementLine).where(SettlementLine.settlement_id == settlement.id)
            )
        ).scalars().all():
            line.amount_net = _money(line.amount_net * factor)
            line.price_per_portion = round(line.price_per_portion * factor, 2)
            line.product_name = f"{line.product_name} (+10% késedelmi felár)"[:256]
        total_net *= factor
        settlement.late_fee_pct = LATE_FEE_PCT
        ref_s, ref_day = overdue
        settlement.note = (
            f"{settlement.note}\nKésedelmi felár 10% az ÁSZF alapján — lejárt "
            f"tartozás: {ref_s.created_at.date().isoformat()} elszámolás, "
            f"fizetési határidő: {ref_day.isoformat()}"
        )

    settlement.total_net = _money(total_net)
    settlement.total_gross = _money(total_net * 1.27)
    if c.payment_terms_days:
        settlement.due_date = today + timedelta(days=int(c.payment_terms_days))

    c.rental_last_billed = today
    db.add(AuditEvent(
        action="settlement.fixed_rental", entity_type="settlement",
        entity_id=str(settlement.id),
        detail={"partner": partner.name, "gross": settlement.total_gross,
                "machines": len(assets), "merged_sites": len(partners),
                "late_fee": overdue is not None},
    ))
    await db.commit()

    # Számla azonnal (a kézi auto-billing mintájára) — hiba esetén az
    # elszámolás megmarad, a státusz error, az admin TG-t kap.
    invoice_err = None
    if settlement.total_net > 0:
        from app.services.wfm.invoicing import create_invoice_for_settlement

        try:
            document_id, mode, due = await create_invoice_for_settlement(
                db, settlement, partner,
            )
            settlement.invoiced = True
            settlement.billingo_document_id = document_id
            settlement.billingo_status = mode
            settlement.payment_status = "outstanding"
            if due:
                settlement.due_date = due
            await db.commit()
        except Exception as exc:  # noqa: BLE001 — best effort, de jelezzük
            logger.warning("fixed rental invoice failed for %s", settlement.id, exc_info=True)
            settlement.billingo_status = "error"
            invoice_err = str(exc)[:200]
            await db.commit()

    # Telegram a vezetőknek — best effort
    try:
        from app.services.wfm.automation import fire_event

        fire_event("rental.billed", {
            "partner_nev": partner.name,
            "osszeg": settlement.total_gross,
            "gepek": len(assets),
            "honap": month_label,
            "szamla": settlement.billingo_document_id or ("HIBA: " + invoice_err if invoice_err else "nincs"),
        })
    except Exception:  # pragma: no cover
        pass
    logger.info(
        "fixed rental billed: %s — %s Ft (%d gép)",
        partner.name, settlement.total_gross, len(assets),
    )
    return True
