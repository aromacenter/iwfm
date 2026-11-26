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


def first_billing_date(billing_day: int, start: date) -> date:
    """172. kör: az első számlázási nap, ami NEM korábbi a kezdésnél (a
    szerződés létrehozása / érvényesség kezdete): ha a hónap számlázási napja
    már elmúlt, a következő hónapé."""
    cand = date(start.year, start.month, _effective_billing_day(billing_day, start))
    if cand >= start:
        return cand
    y, m = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    nxt = date(y, m, 1)
    return date(y, m, _effective_billing_day(billing_day, nxt))


def contract_start(c: PartnerContract, today: date | None = None) -> date:
    """A szerződés „kezdése” az első számlázáshoz: a létrehozás napja és az
    érvényesség kezdete közül a későbbi (régi, created_at nélküli sornál a
    valid_from). A jövőbeli created_at-ot (szimulált, múltbeli futás) nem nézzük."""
    start = c.valid_from
    created = getattr(c, "created_at", None)
    if created is not None and (today is None or created.date() <= today):
        start = max(start, created.date())
    return start


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


CYCLE_DAYS = {"weekly": 7, "biweekly": 14, "four_weekly": 28}
CYCLE_LABEL = {"monthly": "hó", "weekly": "hét", "biweekly": "2 hét", "four_weekly": "4 hét"}


def _local_now(now: datetime | None) -> datetime:
    from zoneinfo import ZoneInfo

    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(ZoneInfo("Europe/Budapest"))


def _is_due(c: PartnerContract, today: date, now_local: datetime) -> bool:
    """Esedékes-e MA a szerződés automata elszámolása (126. kör): ciklus szerint
    (havonta a billing_day napján / hetente, 2 vagy 4 hetente a billing_weekday
    napján), és csak a billing_time (helyi) időpont után."""
    if c.billing_time:
        try:
            hh, mm = (int(x) for x in c.billing_time.split(":"))
            if (now_local.hour, now_local.minute) < (hh, mm):
                return False
        except ValueError:
            pass
    cycle = c.billing_cycle or "monthly"
    last = c.rental_last_billed
    if cycle == "monthly":
        eff_day = _effective_billing_day(int(c.billing_day or 1), today)
        if today.day < eff_day:
            return False
        if last and last.year == today.year and last.month == today.month:
            return False
        if last is None and today < first_billing_date(int(c.billing_day or 1), contract_start(c, today)):
            return False  # 172. kör: a létrehozás hónapjában már elmúlt nap → következő hónap
        return True
    period = CYCLE_DAYS.get(cycle, 7)
    if last is None:
        if today < contract_start(c, today):
            return False
        return today.weekday() == int(c.billing_weekday if c.billing_weekday is not None else 0)
    return (today - last).days >= period


AUTO_SETTLED_BY = "Automata bérleti számlázás"
RESEND_WINDOW_HOURS = 48  # ennyi ideig próbáljuk újra a ki nem küldött számlát
_resend_last_try: dict[str, float] = {}  # settlement id → monotonic idő (óránként egy próba)


def invoice_recipients(settlement: Settlement, partners: list) -> list[str]:
    """A számla címzettjei: az elszámolás saját címzettjei, annak híján a
    partner(ek) e-mailje — összevont számlánál minden érintett cím, duplázás nélkül."""
    from app.services.wfm.email_service import split_addresses

    out: list[str] = []
    sources = [settlement.invoice_emails] if settlement.invoice_emails else [p.contact_email for p in partners]
    for src in sources:
        for e in split_addresses(src):
            if "@" in e and e.lower() not in {x.lower() for x in out}:
                out.append(e)
    return out


async def send_settlement_invoice(db: AsyncSession, settlement: Settlement, partners: list) -> bool:
    """A kiállított számla kiküldése a Billingón át (172. kör). Siker: receipt_sent_at
    beáll; hiba vagy hiányzó cím: figyelmeztetés a naplóba, a háttér-újrapróbálás
    (resend_unsent_invoices) 48 órán át óránként próbálja."""
    from app.services.wfm.invoicing import send_invoice_email

    if not settlement.billingo_document_id:
        return False
    emails = invoice_recipients(settlement, partners)
    if not emails:
        logger.warning("számla-küldés: nincs e-mail cím (%s, %s)", settlement.id, partners[0].name if partners else "?")
        return False
    try:
        await send_invoice_email(db, settlement.billingo_document_id, emails, settlement.invoicing_company)
    except Exception:
        logger.warning("számla-küldés sikertelen (%s) — újrapróbáljuk", settlement.id, exc_info=True)
        return False
    settlement.receipt_sent_at = settlement.receipt_sent_at or datetime.now(UTC)
    await db.commit()
    logger.info("számla kiküldve: %s → %s", settlement.billingo_document_number or settlement.billingo_document_id, ", ".join(emails))
    return True


async def resend_unsent_invoices(db: AsyncSession, now: datetime | None = None) -> int:
    """Biztonsági háló (172. kör): az elmúlt 48 órában kiállított, de ki nem
    küldött számlák újraküldése (óránként egy próba számlánként). A háttérhurok hívja."""
    import time as _time

    now = now or datetime.now(UTC)
    since = now - timedelta(hours=RESEND_WINDOW_HOURS)
    rows = (
        await db.execute(
            select(Settlement).where(
                Settlement.invoiced.is_(True),
                Settlement.billingo_document_id.is_not(None),
                Settlement.receipt_sent_at.is_(None),
                Settlement.created_at >= since,
            ).order_by(Settlement.created_at).limit(50)
        )
    ).scalars().all()
    sent = 0
    mono = _time.monotonic()
    for s in rows:
        key = str(s.id)
        if mono - _resend_last_try.get(key, -1e9) < 3600:
            continue
        _resend_last_try[key] = mono
        partner = (await db.execute(select(Partner).where(Partner.id == s.partner_id))).scalar_one_or_none()
        if partner is None:
            continue
        if await send_settlement_invoice(db, s, [partner]):
            sent += 1
    # takarítás: a 48 órán túli kulcsok
    if len(_resend_last_try) > 500:
        _resend_last_try.clear()
    return sent
RESTOCK_TASK_TITLE = "Készlet-ellenőrzés és feltöltés: {partner}"


def next_workday(d: date) -> date:
    """Ugyanaz a nap, ha munkanap; különben a következő munkanap (ünnep,
    hétvége, áthelyezett pihenőnap figyelembevételével)."""
    from app.services.wfm.holidays import is_non_working_day, is_worked_saturday

    for _ in range(14):
        if is_worked_saturday(d) or (d.weekday() < 5 and not is_non_working_day(d)):
            return d
        d += timedelta(days=1)
    return d


async def _restock_task(db: AsyncSession, partner: Partner, today: date) -> bool:
    """Feladat a partner képviselőjének (Partner.agent_user_id → Employee):
    havonta egyszer, a számlázás munkanapjára. Képviselő vagy karton nélkül
    nem készül (naplózva)."""
    from app.api.tasks import _next_worksheet_serial
    from app.models import Employee, Task, Worksheet

    if not partner.agent_user_id:
        logger.info("fixed rental restock task: nincs képviselő (%s)", partner.name)
        return False
    emp = (await db.execute(select(Employee).where(Employee.user_id == partner.agent_user_id))).scalars().first()
    if emp is None:
        logger.info("fixed rental restock task: a képviselőnek nincs dolgozó-kartonja (%s)", partner.name)
        return False
    title = RESTOCK_TASK_TITLE.format(partner=partner.name)[:256]
    due = next_workday(today)
    month_start = today.replace(day=1)
    dup = (await db.execute(
        select(Task).where(Task.employee_id == emp.id, Task.title == title, Task.due_date >= month_start).limit(1)
    )).scalars().first()
    if dup is not None:
        return False
    task = Task(
        title=title,
        description=(
            f"Fix bérleti díjas partner — a havi számlázással egy időben ({today.isoformat()}) "
            "ellenőrizd az árukészletet a helyszínen, és töltsd fel, ami hiányzik."
        ),
        employee_id=emp.id,
        due_date=due,
    )
    db.add(task)
    await db.flush()
    db.add(Worksheet(task_id=task.id, serial=await _next_worksheet_serial(db), work_description=""))
    await db.commit()
    return True


async def run_fixed_rental_billing(db: AsyncSession, now: datetime | None = None) -> int:
    """Esedékes fix bérleti szerződések számlázása. Visszaadja, hány
    elszámolás készült (a háttérhurok hívja, best-effort)."""
    now_local = _local_now(now)
    today = now_local.date()
    contracts = (
        await db.execute(
            select(PartnerContract).where(
                PartnerContract.fixed_rental.is_(True),
                PartnerContract.auto_settlement.is_(True),
                PartnerContract.valid_from <= today,
                (PartnerContract.valid_to.is_(None))
                | (PartnerContract.valid_to >= today),
            )
        )
    ).scalars().all()
    created = 0
    # 171. kör: azonos adószámú címek SAJÁT fix szerződéssel, mind összevonásra
    # állítva és azonos számlázási beállítással → EGY számla a legrégebbi
    # szerződés partnerére, szerződésenként egy tétellel
    contracts = sorted(contracts, key=lambda x: (x.valid_from, str(x.partner_id)))
    co_map, skip = await _merge_groups(db, contracts, today, now_local)
    for c in contracts:
        if c.id in skip:
            continue
        try:
            if await _bill_contract(db, c, today, now_local, co_contracts=co_map.get(c.id)):
                created += 1
        except Exception:
            logger.exception("fixed rental billing failed for contract %s", c.id)
            await db.rollback()
    return created


def _same_billing(a: PartnerContract, b: PartnerContract) -> bool:
    """Összevonható-e két fix szerződés egy számlára: azonos ciklus, nap,
    fizetési mód és határidő (a számlán egy esedékesség van)."""
    cycle = a.billing_cycle or "monthly"
    if cycle != (b.billing_cycle or "monthly"):
        return False
    if cycle == "monthly":
        if int(a.billing_day or 1) != int(b.billing_day or 1):
            return False
    elif (a.billing_weekday or 0) != (b.billing_weekday or 0):
        return False
    return (
        (a.payment_method or "transfer") == (b.payment_method or "transfer")
        and (a.payment_terms_days or 0) == (b.payment_terms_days or 0)
    )


async def _merge_groups(
    db: AsyncSession, contracts: list, today: date, now_local: datetime
) -> tuple[dict, set]:
    """171. kör: {elsődleges szerződés id → [(társ-szerződés, partner)]} és a
    társként számlázott szerződések halmaza (ezeket a fő kör kihagyja).
    Elsődleges a legrégebbi szerződés (a lista valid_from szerint rendezett)."""
    co: dict = {}
    skip: set = set()
    pids = {c.partner_id for c in contracts}
    partners = {
        p.id: p for p in (await db.execute(select(Partner).where(Partner.id.in_(pids)))).scalars().all()
    } if pids else {}
    for c in contracts:
        if c.id in skip or not c.rental_merge_company:
            continue
        p = partners.get(c.partner_id)
        tax = (p.tax_number or "").strip() if p else ""
        if not p or not p.is_active or not tax:
            continue
        sibs = []
        for o in contracts:
            if o.id == c.id or o.id in skip or not o.rental_merge_company:
                continue
            op = partners.get(o.partner_id)
            if not op or op.id == p.id or not op.is_active or (op.tax_number or "").strip() != tax:
                continue
            if not _same_billing(c, o):
                logger.info("fixed rental: %s és %s nem vonható össze (eltérő számlázási beállítás)", p.name, op.name)
                continue
            if not _is_due(o, today, now_local):
                continue  # pl. már számlázva ebben a hónapban
            sibs.append((o, op))
        if sibs:
            co[c.id] = sibs
            skip.update(o.id for o, _ in sibs)
    return co, skip


async def _bill_contract(
    db: AsyncSession, c: PartnerContract, today: date, now_local: datetime | None = None,
    co_contracts: list | None = None,
) -> bool:
    if (c.billing_cycle or "monthly") == "monthly" and c.billing_day is None:
        return False
    if not _is_due(c, today, now_local or _local_now(None)):
        return False

    partner = (
        await db.execute(select(Partner).where(Partner.id == c.partner_id))
    ).scalar_one_or_none()
    if partner is None or not partner.is_active:
        return False
    co_contracts = co_contracts or []  # 171. kör: társ-szerződések (saját díjjal) ugyanerre a számlára

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

    # szerződés-díj partnerenként: az elsődleges + a társ-szerződések (171. kör);
    # a saját szerződés nélküli társcímeknél 0 → gépenkénti díj
    contract_fee = float(c.rent_fee or 0)
    fee_by_partner: dict = {partner.id: contract_fee}
    for o, op in co_contracts:
        fee_by_partner[op.id] = float(o.rent_fee or 0)
    all_partners = partners + [op for _o, op in co_contracts]
    assets: list[tuple[Asset, Partner]] = []
    for p in all_partners:
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
        assets.extend((a, p) for a in rows if (a.rent_fee or 0) > 0 or fee_by_partner.get(p.id, 0) > 0)
    if not assets and not any(v > 0 for v in fee_by_partner.values()):
        logger.info("fixed rental: nincs díjas gép (%s) — kihagyva", partner.name)
        c.rental_last_billed = today  # ne próbálkozzon percenként egész hónapban
        for o, _op in co_contracts:
            o.rental_last_billed = today
        await db.commit()
        return False

    cycle = c.billing_cycle or "monthly"
    if cycle == "monthly":
        month_label = f"{today.year}. {today.month:02d}. hó"
    else:
        period_start = today - timedelta(days=CYCLE_DAYS.get(cycle, 7) - 1)
        month_label = f"{period_start.isoformat()} – {today.isoformat()} ({CYCLE_LABEL.get(cycle, cycle)})"
    settlement = Settlement(
        partner_id=partner.id,
        settled_by_user_id=None,
        settled_by_name=AUTO_SETTLED_BY,
        invoicing_company=partner.invoicing_company,
        payment_method=c.payment_method or "transfer",
        due_days=c.payment_terms_days,
        note=f"Fix bérleti díj — {month_label}" + (
            " — összevont számla: " + ", ".join(op.name for _o, op in co_contracts) if co_contracts else ""
        ),
    )
    db.add(settlement)
    await db.flush()

    total_net = 0.0
    # 124. kör: a díj a SZERZŐDÉSÉ — egy tétel, a gépek felsorolva a megnevezésben,
    # gépcsere nem változtatja az összeget; 171. kör: a társ-szerződések díja is
    # egy-egy tétel, a cím nevével
    for p_fee, fee in [(partner, contract_fee)] + [(op, float(o.rent_fee or 0)) for o, op in co_contracts]:
        if fee <= 0:
            continue
        machines = ", ".join(
            " ".join(x for x in (a.manufacturer, a.name) if x) + f" ({a.barcode})"
            for a, p in assets if p.id == p_fee.id
        )
        site = f" — {p_fee.name}" if co_contracts else ""
        line_name = f"{RENTAL_LINE_NAME} — {month_label}{site}" + (f" — {machines}" if machines else "")
        db.add(SettlementLine(
            settlement_id=settlement.id,
            product_id=None,
            product_name=line_name[:256],
            previous_qty=0.0, physical_qty=0.0, consumed_qty=0.0,
            portions=1.0,  # 1 hónap
            price_per_portion=fee,
            vat_percent=27,
            amount_net=_money(fee),
        ))
        total_net += fee
    # gépenkénti díj csak ott, ahol nincs szerződés-díj (saját szerződés nélküli
    # társcímek, vagy régi, gép-díjas szerződés)
    assets = [(a, p) for a, p in assets if fee_by_partner.get(p.id, 0) <= 0 and (a.rent_fee or 0) > 0]
    for a, p in assets:
        site = f" ({p.name})" if (c.rental_merge_company and len(all_partners) > 1) else ""
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
    for o, _op in co_contracts:
        o.rental_last_billed = today  # a társ-szerződés ezen a számlán van
    db.add(AuditEvent(
        action="settlement.fixed_rental", entity_type="settlement",
        entity_id=str(settlement.id),
        detail={"partner": partner.name, "gross": settlement.total_gross,
                "machines": len(assets), "merged_sites": len(all_partners),
                "merged_contracts": [op.name for _o, op in co_contracts] or None,
                "late_fee": overdue is not None},
    ))
    await db.commit()
    # 168. kör (125b8394): a számlázás napján (vagy a következő munkanapon)
    # a partner képviselője feladatot kap: készlet ellenőrzése és feltöltése
    for rp in [partner] + [op for _o, op in co_contracts]:
        try:
            await _restock_task(db, rp, today)
        except Exception:
            logger.exception("fixed rental restock task failed for %s", rp.name)
            await db.rollback()

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
            # 172. kör: MINDEN számlát a Billingó küld ki — az automatát is,
            # a partner (és az összevont társcímek) e-mail címére
            await send_settlement_invoice(db, settlement, [partner] + [op for _o, op in co_contracts])
        except Exception as exc:  # noqa: BLE001 — best effort, de jelezzük
            logger.warning("fixed rental invoice failed for %s", settlement.id, exc_info=True)
            settlement.billingo_status = "error"
            invoice_err = str(exc)[:200]
            await db.commit()

    # Telegram a vezetőknek — best effort
    try:
        from app.services.wfm.automation import fire_event

        fire_event("rental.billed", {
            "_settlement_id": str(settlement.id),
            "_partner_id": partner.id,
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
