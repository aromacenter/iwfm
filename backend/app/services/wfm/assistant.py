"""AI-asszisztens: chat eszköz-használattal (agentikus hurok).

A modell a regisztrált eszközökkel (TOOLS) tud dolgozni: partnert keres,
szervizjegyet vesz fel, számlálót rögzít, készletet tölt fel, munkalapot tölt
ki, jegyzetet fűz partnerhez, és az admin felületen navigál. Minden eszköz a
BEJELENTKEZETT felhasználó jogosultságaival fut (permission-mátrix), és
auditálódik.

Szolgáltató-réteg: Anthropic natív tool-use, Gemini function calling — a
``_provider_step`` absztrakció mögött (a tesztek ezt patchelik).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_permission_matrix, record_audit
from app.core.crypto import decrypt_pii
from app.models import (
    Asset,
    AssetMovement,
    Employee,
    Partner,
    PartnerStock,
    Product,
    ServiceTicket,
    Settlement,
    StockMovement,
    Task,
    User,
    Worksheet,
)
from app.services.wfm import ai_service

logger = logging.getLogger(__name__)

MAX_STEPS = 8

# A navigate eszköz csak ezekre az útvonalakra engedhet.
NAV_PATHS = (
    "/", "/vezerlopult", "/gepek", "/partnerek", "/elszamolas", "/termekek",
    "/uzletkoto", "/szerviz", "/tudasbazis", "/feladatok", "/feladataim",
    "/dolgozok", "/beosztas", "/jelenlet", "/tavollet", "/naplo",
    "/beallitasok", "/import-export", "/berexport", "/sajat-beosztas",
)


# ─── Segédek ─────────────────────────────────────────────────────────────────


def _has(feature: str, actor: User, matrix: dict[str, list[str]]) -> bool:
    return actor.role == "admin" or feature in matrix.get(actor.role, [])


async def _find_partner(db: AsyncSession, ref: str) -> Partner | None:
    ref = (ref or "").strip()
    if not ref:
        return None
    try:
        pid = uuid.UUID(ref)
        return (await db.execute(select(Partner).where(Partner.id == pid))).scalar_one_or_none()
    except ValueError:
        pass
    exact = (
        await db.execute(
            select(Partner).where(
                or_(
                    func.lower(Partner.name) == ref.lower(),
                    func.lower(Partner.partner_code) == ref.lower(),
                )
            )
        )
    ).scalars().first()
    if exact:
        return exact
    like = f"%{ref}%"
    matches = (
        (await db.execute(select(Partner).where(Partner.name.ilike(like)).limit(2)))
        .scalars()
        .all()
    )
    return matches[0] if len(matches) == 1 else None


async def _find_asset(db: AsyncSession, ref: str) -> Asset | None:
    ref = (ref or "").strip()
    if not ref:
        return None
    return (
        await db.execute(select(Asset).where(Asset.barcode == ref))
    ).scalar_one_or_none()


def _partner_brief(p: Partner) -> dict:
    return {
        "id": str(p.id), "code": p.partner_code, "name": p.name,
        "company_name": p.company_name, "city": p.address_city,
        "contact": p.contact_name, "phone": p.contact_phone,
        "active": p.is_active,
    }


# ─── Eszköz-végrehajtók ──────────────────────────────────────────────────────
# Mind (db, actor, args) → (result_dict, event | None). Hiba esetén a result
# {"error": ...} — a modell ebből tud korrigálni.


async def _t_search_partners(db: AsyncSession, actor: User, args: dict):
    q = str(args.get("query") or "").strip()
    like = f"%{q}%"
    rows = (
        (
            await db.execute(
                select(Partner)
                .where(
                    or_(
                        Partner.name.ilike(like),
                        Partner.company_name.ilike(like),
                        Partner.partner_code.ilike(like),
                        Partner.tax_number.ilike(like),
                        Partner.address_city.ilike(like),
                        Partner.contact_name.ilike(like),
                    )
                )
                .order_by(Partner.name)
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    return {"partners": [_partner_brief(p) for p in rows], "count": len(rows)}, None


async def _t_partner_details(db: AsyncSession, actor: User, args: dict):
    p = await _find_partner(db, str(args.get("partner") or ""))
    if p is None:
        return {"error": "partner nem található (adj meg pontos nevet vagy PT-kódot)"}, None
    stock_rows = (
        await db.execute(
            select(PartnerStock, Product.name, Product.unit)
            .join(Product, Product.id == PartnerStock.product_id)
            .where(PartnerStock.partner_id == p.id)
        )
    ).all()
    last = (
        await db.execute(
            select(Settlement)
            .where(Settlement.partner_id == p.id)
            .order_by(Settlement.created_at.desc())
            .limit(3)
        )
    ).scalars().all()
    assets = (
        await db.execute(select(Asset).where(Asset.partner_id == p.id).limit(20))
    ).scalars().all()
    out = _partner_brief(p)
    out.update({
        "address": p.address, "billing_address": p.billing_address,
        "tax_number": p.tax_number, "notes": (p.notes or "")[:500],
        "stock": [
            {"product": name, "quantity": s.quantity, "unit": unit}
            for s, name, unit in stock_rows
        ],
        "machines": [
            {"barcode": a.barcode, "name": a.name, "counter": a.counter,
             "customer_owned": a.customer_owned}
            for a in assets
        ],
        "last_settlements": [
            {"date": f"{s.created_at:%Y-%m-%d}", "gross": s.total_gross,
             "by": s.settled_by_name}
            for s in last
        ],
    })
    return out, None


async def _t_search_machines(db: AsyncSession, actor: User, args: dict):
    q = str(args.get("query") or "").strip()
    like = f"%{q}%"
    rows = (
        (
            await db.execute(
                select(Asset, Partner.name)
                .outerjoin(Partner, Partner.id == Asset.partner_id)
                .where(
                    or_(
                        Asset.barcode.ilike(like),
                        Asset.name.ilike(like),
                        Asset.serial_number.ilike(like),
                        Asset.manufacturer.ilike(like),
                    )
                )
                .limit(10)
            )
        ).all()
    )
    return {
        "machines": [
            {
                "barcode": a.barcode, "name": a.name, "manufacturer": a.manufacturer,
                "serial": a.serial_number, "counter": a.counter, "status": a.status,
                "customer_owned": a.customer_owned, "partner": pname,
            }
            for a, pname in rows
        ],
        "count": len(rows),
    }, None


async def _t_create_service_ticket(db: AsyncSession, actor: User, args: dict):
    from app.api.service import _next_ticket_no

    title = str(args.get("title") or "").strip()
    if not title:
        return {"error": "title kötelező"}, None
    priority = str(args.get("priority") or "normal")
    if priority not in ("low", "normal", "high"):
        priority = "normal"
    asset = None
    if args.get("machine_barcode"):
        asset = await _find_asset(db, str(args["machine_barcode"]))
        if asset is None:
            return {"error": f"gép nem található: {args['machine_barcode']}"}, None
    partner = None
    if args.get("partner"):
        partner = await _find_partner(db, str(args["partner"]))
    if partner is None and asset is not None and asset.partner_id:
        partner = (
            await db.execute(select(Partner).where(Partner.id == asset.partner_id))
        ).scalar_one_or_none()

    tk = ServiceTicket(
        ticket_no=await _next_ticket_no(db),
        kind="repair",
        status="open",
        priority=priority,
        title=title[:256],
        description=str(args.get("description") or "") or None,
        asset_id=asset.id if asset else None,
        asset_label=f"{asset.name} ({asset.barcode})" if asset else None,
        partner_id=partner.id if partner else None,
        partner_label=partner.name if partner else None,
        created_by=actor.id,
    )
    db.add(tk)
    await db.flush()
    await record_audit(
        db, actor=actor, action="service.create", entity_type="service_ticket",
        entity_id=tk.ticket_no, detail={"via": "assistant"},
    )
    await db.commit()
    return (
        {"ok": True, "ticket_no": tk.ticket_no, "title": tk.title},
        {"kind": "created", "label": f"Szervizjegy: {tk.ticket_no} — {tk.title}"},
    )


async def _t_report_counter(db: AsyncSession, actor: User, args: dict):
    asset = await _find_asset(db, str(args.get("machine_barcode") or ""))
    if asset is None:
        return {"error": f"gép nem található: {args.get('machine_barcode')}"}, None
    try:
        counter = int(args.get("counter"))
    except (TypeError, ValueError):
        return {"error": "counter: egész szám kell"}, None
    if counter < 0 or counter > 100_000_000:
        return {"error": "counter: 0 és 100 millió között"}, None
    old = asset.counter
    asset.counter = counter
    detail = f"Számláló: {old if old is not None else '—'} → {counter} · asszisztens ({actor.display_name})"
    if old is not None and counter < old:
        detail += " (csökkenés!)"
    db.add(AssetMovement(asset_id=asset.id, action="counter", detail=detail, actor_user_id=actor.id))
    await record_audit(
        db, actor=actor, action="asset.counter", entity_type="asset",
        entity_id=asset.barcode, detail={"via": "assistant", "counter": counter},
    )
    await db.commit()
    return (
        {"ok": True, "barcode": asset.barcode, "old_counter": old, "new_counter": counter},
        {"kind": "updated", "label": f"Számláló rögzítve: {asset.barcode} → {counter}"},
    )


async def _t_replenish_stock(db: AsyncSession, actor: User, args: dict):
    partner = await _find_partner(db, str(args.get("partner") or ""))
    if partner is None:
        return {"error": "partner nem található"}, None
    pq = str(args.get("product") or "").strip()
    product = (
        await db.execute(
            select(Product).where(Product.name.ilike(f"%{pq}%"), Product.is_active.is_(True)).limit(2)
        )
    ).scalars().all()
    if len(product) != 1:
        return {"error": f"termék nem egyértelmű vagy nem található: '{pq}'"}, None
    prod = product[0]
    try:
        qty = float(args.get("quantity"))
    except (TypeError, ValueError):
        return {"error": "quantity: szám kell"}, None
    if qty <= 0 or qty > 10000:
        return {"error": "quantity: 0 és 10000 között"}, None
    stock = (
        await db.execute(
            select(PartnerStock).where(
                PartnerStock.partner_id == partner.id, PartnerStock.product_id == prod.id
            )
        )
    ).scalar_one_or_none()
    if stock is None:
        stock = PartnerStock(partner_id=partner.id, product_id=prod.id, quantity=0.0)
        db.add(stock)
    stock.quantity = round(stock.quantity + qty, 3)
    db.add(StockMovement(
        partner_id=partner.id, product_id=prod.id, action="replenish",
        quantity_delta=qty, note="asszisztens", actor_user_id=actor.id,
    ))
    await record_audit(
        db, actor=actor, action="stock.replenish", entity_type="partner",
        entity_id=str(partner.id), detail={"via": "assistant", "product": prod.name, "qty": qty},
    )
    await db.commit()
    return (
        {"ok": True, "partner": partner.name, "product": prod.name,
         "added": qty, "new_quantity": stock.quantity, "unit": prod.unit},
        {"kind": "updated",
         "label": f"Feltöltve: {partner.name} · {prod.name} +{qty:g} {prod.unit}"},
    )


async def _t_add_partner_note(db: AsyncSession, actor: User, args: dict):
    partner = await _find_partner(db, str(args.get("partner") or ""))
    if partner is None:
        return {"error": "partner nem található"}, None
    note = str(args.get("note") or "").strip()
    if not note:
        return {"error": "note kötelező"}, None
    stamp = f"[{date.today():%Y-%m-%d} · {actor.display_name}] {note}"
    partner.notes = f"{partner.notes}\n{stamp}" if partner.notes else stamp
    await record_audit(
        db, actor=actor, action="partner.note", entity_type="partner",
        entity_id=str(partner.id), detail={"via": "assistant"},
    )
    await db.commit()
    return (
        {"ok": True, "partner": partner.name},
        {"kind": "updated", "label": f"Jegyzet mentve: {partner.name}"},
    )


async def _t_list_products(db: AsyncSession, actor: User, args: dict):
    q = str(args.get("query") or "").strip()
    stmt = select(Product).where(Product.is_active.is_(True)).order_by(Product.name).limit(15)
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "products": [
            {"name": p.name, "unit": p.unit, "price_per_portion": p.price_per_portion}
            for p in rows
        ]
    }, None


async def _t_create_task(db: AsyncSession, actor: User, args: dict):
    from app.api.tasks import _next_worksheet_serial

    title = str(args.get("title") or "").strip()
    if not title:
        return {"error": "title kötelező"}, None
    emp_ref = str(args.get("employee") or "").strip().lower()
    emps = (await db.execute(select(Employee).where(Employee.status == "active"))).scalars().all()
    target = None
    if emp_ref:
        for e in emps:
            full = f"{e.last_name} {e.first_name}".lower()
            if emp_ref in full or full in emp_ref:
                target = e
                break
        if target is None:
            return {"error": f"dolgozó nem található: '{args.get('employee')}'. "
                             f"Elérhető: {', '.join(f'{e.last_name} {e.first_name}' for e in emps[:10])}"}, None
    else:
        target = next((e for e in emps if e.user_id == actor.id), None)
        if target is None:
            return {"error": "add meg, melyik dolgozónak szól a feladat (employee)"}, None
    try:
        due = date.fromisoformat(str(args.get("due_date"))) if args.get("due_date") else date.today()
    except ValueError:
        return {"error": "due_date: ÉÉÉÉ-HH-NN formátum"}, None

    task = Task(
        title=title[:256],
        description=str(args.get("description") or "") or None,
        employee_id=target.id,
        due_date=due,
        created_by=actor.id,
    )
    db.add(task)
    await db.flush()
    ws = Worksheet(
        task_id=task.id,
        serial=await _next_worksheet_serial(db),
        work_description="",
        created_by=actor.id,
    )
    db.add(ws)
    await record_audit(
        db, actor=actor, action="task.create", entity_type="task",
        entity_id=str(task.id), detail={"via": "assistant", "worksheet": ws.serial},
    )
    await db.commit()
    return (
        {"ok": True, "task_id": str(task.id), "worksheet_serial": ws.serial,
         "employee": f"{target.last_name} {target.first_name}", "due_date": str(due)},
        {"kind": "created", "label": f"Feladat + munkalap: {ws.serial} — {title}"},
    )


async def _t_fill_worksheet(db: AsyncSession, actor: User, args: dict, *, own_only: bool):
    serial = str(args.get("worksheet_serial") or "").strip()
    ws = None
    if serial:
        ws = (
            await db.execute(select(Worksheet).where(Worksheet.serial == serial))
        ).scalar_one_or_none()
        if ws is None:
            return {"error": f"munkalap nem található: {serial}"}, None
    else:
        # sorszám híján: a hívó legutóbbi nyitott feladatának munkalapja
        emp = (
            await db.execute(select(Employee).where(Employee.user_id == actor.id))
        ).scalar_one_or_none()
        if emp is None:
            return {"error": "add meg a munkalap sorszámát (ML-...)"}, None
        task = (
            await db.execute(
                select(Task)
                .where(Task.employee_id == emp.id, Task.status == "open")
                .order_by(Task.due_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if task is None:
            return {"error": "nincs nyitott feladatod; add meg a munkalap sorszámát"}, None
        ws = (
            await db.execute(select(Worksheet).where(Worksheet.task_id == task.id))
        ).scalar_one_or_none()
        if ws is None:
            return {"error": "a feladathoz nincs munkalap"}, None

    if own_only:
        task = (await db.execute(select(Task).where(Task.id == ws.task_id))).scalar_one_or_none()
        emp = (
            await db.execute(select(Employee).where(Employee.user_id == actor.id))
        ).scalar_one_or_none()
        if task is None or emp is None or task.employee_id != emp.id:
            return {"error": "ez a munkalap nem a tiéd"}, None

    changed = []
    if args.get("work_description"):
        ws.work_description = str(args["work_description"])
        changed.append("munkavégzés leírása")
    if isinstance(args.get("materials"), list):
        mats = []
        for m in args["materials"][:30]:
            if isinstance(m, dict) and m.get("name"):
                mats.append({
                    "name": str(m["name"])[:200],
                    "qty": str(m.get("qty") or m.get("quantity") or "")[:32],
                    "unit": str(m.get("unit") or "db")[:16],
                })
        ws.materials = mats
        changed.append("anyagok")
    if args.get("hours") is not None:
        try:
            ws.hours_spent = float(args["hours"])
            changed.append("munkaóra")
        except (TypeError, ValueError):
            return {"error": "hours: szám kell"}, None
    if args.get("client_name"):
        ws.client_name = str(args["client_name"])[:256]
        changed.append("ügyfél neve")
    if args.get("client_location"):
        ws.client_location = str(args["client_location"])[:512]
        changed.append("helyszín")
    if not changed:
        return {"error": "nincs kitöltendő mező az argumentumok között"}, None
    await record_audit(
        db, actor=actor, action="worksheet.update", entity_type="worksheet",
        entity_id=ws.serial, detail={"via": "assistant", "fields": changed},
    )
    await db.commit()
    return (
        {"ok": True, "worksheet_serial": ws.serial, "updated": changed},
        {"kind": "updated", "label": f"Munkalap kitöltve: {ws.serial} ({', '.join(changed)})"},
    )


async def _t_create_settlement(db: AsyncSession, actor: User, args: dict):
    """Elszámolás rögzítése diktálásból — a webes create_settlement logikája:
    fizikai leltár → fogyás → adag × ár, készlet frissítés, mozgás."""
    from app.api.consignment import (
        _effective_price,
        _partner_price_map,
        apply_contract_lines,
        portions_from,
    )
    from app.models import Settlement, SettlementLine

    partner = await _find_partner(db, str(args.get("partner") or ""))
    if partner is None:
        return {"error": "partner nem található (pontos név vagy PT-kód kell)"}, None
    raw_lines = args.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        return {"error": "lines kötelező: [{product, physical_qty}]"}, None
    payment = str(args.get("payment_method") or "cash")
    if payment not in ("cash", "card", "transfer"):
        return {"error": "payment_method: cash | card | transfer"}, None

    settlement = Settlement(
        partner_id=partner.id,
        settled_by_user_id=actor.id,
        settled_by_name=actor.display_name,
        invoicing_company=partner.invoicing_company,
        payment_method=payment,
        total_net=0.0,
        total_gross=0.0,
        note=(str(args.get("note") or "") or None),
    )
    db.add(settlement)
    await db.flush()

    overrides = await _partner_price_map(db, partner.id)
    total_net = total_gross = 0.0
    summary = []
    for raw in raw_lines[:20]:
        if not isinstance(raw, dict):
            continue
        pq = str(raw.get("product") or "").strip()
        matches = (
            await db.execute(
                select(Product)
                .where(Product.name.ilike(f"%{pq}%"), Product.is_active.is_(True))
                .limit(2)
            )
        ).scalars().all()
        if len(matches) != 1:
            await db.rollback()
            return {"error": f"termék nem egyértelmű vagy nincs meg: '{pq}'"}, None
        product = matches[0]
        try:
            physical = float(raw.get("physical_qty"))
        except (TypeError, ValueError):
            await db.rollback()
            return {"error": f"physical_qty: szám kell ({pq})"}, None
        if physical < 0 or physical > 10000:
            await db.rollback()
            return {"error": "physical_qty: 0 és 10000 között"}, None

        stock = (
            await db.execute(
                select(PartnerStock).where(
                    PartnerStock.partner_id == partner.id,
                    PartnerStock.product_id == product.id,
                )
            )
        ).scalar_one_or_none()
        previous = stock.quantity if stock is not None else 0.0
        consumed = max(previous - physical, 0.0)
        portions = portions_from(consumed, product.grams_per_portion)
        unit_price = _effective_price(product, overrides)
        amount_net = round(portions * unit_price, 2)
        amount_gross = round(amount_net * (1 + product.vat_percent / 100), 2)
        total_net += amount_net
        total_gross += amount_gross

        db.add(SettlementLine(
            settlement_id=settlement.id,
            product_id=product.id,
            product_name=product.name,
            previous_qty=previous,
            physical_qty=physical,
            consumed_qty=consumed,
            portions=portions,
            price_per_portion=unit_price,
            vat_percent=product.vat_percent,
            amount_net=amount_net,
        ))
        if stock is None:
            db.add(PartnerStock(partner_id=partner.id, product_id=product.id, quantity=physical))
        else:
            stock.quantity = physical
        if consumed > 0:
            db.add(StockMovement(
                partner_id=partner.id, product_id=product.id, action="settlement",
                quantity_delta=-consumed, settlement_id=settlement.id,
                actor_user_id=actor.id,
            ))
        summary.append(f"{product.name}: fogyás {consumed:g} kg ({portions:.0f} adag)")

    total_portions = sum(
        line.portions
        for line in (
            await db.execute(
                select(SettlementLine).where(SettlementLine.settlement_id == settlement.id)
            )
        ).scalars()
    )
    extra_net, extra_gross = await apply_contract_lines(db, partner, settlement, total_portions)
    if extra_net:
        summary.append(f"szerződéses tételek (bérleti díj / minimum): {extra_net:.0f} Ft nettó")
    total_net += extra_net
    total_gross += extra_gross

    settlement.total_net = round(total_net, 2)
    settlement.total_gross = round(total_gross, 2)
    await record_audit(
        db, actor=actor, action="settlement.create", entity_type="settlement",
        entity_id=str(settlement.id),
        detail={"partner": partner.name, "gross": settlement.total_gross, "via": "assistant"},
    )
    await db.commit()
    return (
        {
            "ok": True, "settlement_id": str(settlement.id), "partner": partner.name,
            "total_gross": settlement.total_gross, "total_net": settlement.total_net,
            "lines": summary,
        },
        {"kind": "created",
         "label": f"Elszámolás: {partner.name} · {settlement.total_gross:,.0f} Ft".replace(",", " ")},
    )


async def _t_daily_summary(db: AsyncSession, actor: User, args: dict):
    from app.services.wfm.notifier import build_daily_digest

    return {"summary": await build_daily_digest(db)}, None


async def _t_navigate(db: AsyncSession, actor: User, args: dict):
    path = str(args.get("path") or "").strip()
    base = path.split("?")[0].rstrip("/") or "/"
    if base not in NAV_PATHS:
        return {"error": f"ismeretlen útvonal: {path}. Engedélyezett: {', '.join(NAV_PATHS)}"}, None
    return (
        {"ok": True, "path": path},
        {"kind": "navigate", "label": f"Megnyitás: {path}", "path": path},
    )


# ─── Eszköz-regiszter ────────────────────────────────────────────────────────
# feature: melyik jogosultság kell hozzá (None = bármely bejelentkezett user).

TOOLS: list[dict] = [
    {
        "name": "search_partners",
        "feature": "partners",
        "description": "Partnerek keresése névre, PT-kódra, adószámra, városra vagy kapcsolattartóra. Max 10 találat.",
        "schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        "run": _t_search_partners,
    },
    {
        "name": "partner_details",
        "feature": "partners",
        "description": "Egy partner részletei: elérhetőség, készlet, gépei, utolsó elszámolások. A partner lehet név, PT-kód vagy id.",
        "schema": {"type": "object", "properties": {"partner": {"type": "string"}}, "required": ["partner"]},
        "run": _t_partner_details,
    },
    {
        "name": "search_machines",
        "feature": "machines",
        "description": "Gépek keresése vonalkódra, típusra, gyártóra vagy gyári számra. Max 10 találat.",
        "schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        "run": _t_search_machines,
    },
    {
        "name": "create_service_ticket",
        "feature": "service",
        "description": "Szerviz-hibajegy felvétele. machine_barcode: a gép vonalkódja (opcionális); partner: név/PT-kód (opcionális, gép alapján auto); priority: low|normal|high.",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "machine_barcode": {"type": "string"},
                "partner": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "normal", "high"]},
            },
            "required": ["title"],
        },
        "run": _t_create_service_ticket,
    },
    {
        "name": "report_counter",
        "feature": "machines",
        "description": "Gép számláló-állásának rögzítése (a gép adatlapja + előzmények frissülnek).",
        "schema": {
            "type": "object",
            "properties": {
                "machine_barcode": {"type": "string"},
                "counter": {"type": "integer"},
            },
            "required": ["machine_barcode", "counter"],
        },
        "run": _t_report_counter,
    },
    {
        "name": "replenish_stock",
        "feature": "settlements",
        "description": "Partner külső raktárának feltöltése termékkel (bizomány). quantity a termék egységében (pl. kg).",
        "schema": {
            "type": "object",
            "properties": {
                "partner": {"type": "string"},
                "product": {"type": "string"},
                "quantity": {"type": "number"},
            },
            "required": ["partner", "product", "quantity"],
        },
        "run": _t_replenish_stock,
    },
    {
        "name": "add_partner_note",
        "feature": "partners",
        "description": "Dátumozott jegyzet hozzáfűzése egy partner megjegyzéseihez.",
        "schema": {
            "type": "object",
            "properties": {"partner": {"type": "string"}, "note": {"type": "string"}},
            "required": ["partner", "note"],
        },
        "run": _t_add_partner_note,
    },
    {
        "name": "list_products",
        "feature": "products",
        "description": "Aktív termékek listája (opcionális névszűréssel).",
        "schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "run": _t_list_products,
    },
    {
        "name": "create_task",
        "feature": "tasks",
        "description": "Feladat kiosztása dolgozónak — a munkalap (ML-sorszám) automatikusan kiáll. employee: a dolgozó neve; due_date: ÉÉÉÉ-HH-NN (alapból ma).",
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "employee": {"type": "string"},
                "due_date": {"type": "string"},
            },
            "required": ["title"],
        },
        "run": _t_create_task,
    },
    {
        "name": "fill_worksheet",
        "feature": None,  # saját munkalapot bárki; másét tasks-joggal
        "description": "Munkalap kitöltése diktálás/leírás alapján. worksheet_serial (ML-...) vagy enélkül a hívó legutóbbi nyitott feladata. Mezők: work_description, materials [{name, qty, unit}], hours, client_name, client_location.",
        "schema": {
            "type": "object",
            "properties": {
                "worksheet_serial": {"type": "string"},
                "work_description": {"type": "string"},
                "materials": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "qty": {"type": "string"},
                            "unit": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
                "hours": {"type": "number"},
                "client_name": {"type": "string"},
                "client_location": {"type": "string"},
            },
        },
        "run": None,  # speciális: own_only a jogosultságtól függ (lásd _execute_tool)
    },
    {
        "name": "daily_summary",
        "feature": "settlements",
        "description": "Napi összefoglaló: esedékes elszámolások, hamarosan kifogyó partnerek, nyitott rendelések/szervizjegyek, kintlévőség, fogyás-anomáliák. Használd, ha a felhasználó a napi helyzetről kérdez.",
        "schema": {"type": "object", "properties": {}},
        "run": _t_daily_summary,
    },
    {
        "name": "create_settlement",
        "feature": "settlements",
        "description": (
            "Elszámolás rögzítése egy partnernél a FIZIKAI leltár alapján (a fogyás és az "
            "összeg automatikusan számolódik). lines: [{product: terméknév, physical_qty: "
            "megmaradt mennyiség kg-ban}]. FONTOS: végrehajtás előtt mindig sorold fel a "
            "felhasználónak, mit fogsz rögzíteni, és várd meg a megerősítését."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "partner": {"type": "string"},
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product": {"type": "string"},
                            "physical_qty": {"type": "number"},
                        },
                        "required": ["product", "physical_qty"],
                    },
                },
                "payment_method": {"type": "string", "enum": ["cash", "card", "transfer"]},
                "note": {"type": "string"},
            },
            "required": ["partner", "lines"],
        },
        "run": _t_create_settlement,
    },
    {
        "name": "navigate",
        "feature": None,
        "description": "Az admin felület egy oldalának megnyitása a felhasználónak. path pl. /gepek, /partnerek, /elszamolas, /szerviz, /feladatok, /tudasbazis.",
        "schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        "run": _t_navigate,
    },
]

_TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


def _available_tools(actor: User, matrix: dict[str, list[str]]) -> list[dict]:
    out = []
    for t in TOOLS:
        if t["feature"] is None or _has(t["feature"], actor, matrix):
            out.append(t)
    return out


async def _execute_tool(
    db: AsyncSession, actor: User, matrix: dict[str, list[str]], name: str, args: dict
) -> tuple[dict, dict | None]:
    tool = _TOOL_BY_NAME.get(name)
    if tool is None:
        return {"error": f"ismeretlen eszköz: {name}"}, None
    if tool["feature"] is not None and not _has(tool["feature"], actor, matrix):
        return {"error": "ehhez a művelethez nincs jogosultságod"}, None
    try:
        if name == "fill_worksheet":
            own_only = not _has("tasks", actor, matrix)
            return await _t_fill_worksheet(db, actor, args, own_only=own_only)
        return await tool["run"](db, actor, args)
    except Exception as exc:  # noqa: BLE001 — a modell kap hibát, nem a kliens
        logger.exception("assistant tool %s failed", name)
        await db.rollback()
        return {"error": f"belső hiba: {type(exc).__name__}"}, None


# ─── Szolgáltató-réteg ───────────────────────────────────────────────────────


def _system_prompt(actor: User) -> str:
    return (
        "Az Iwfm (kávégép-bizomány és munkaerő-kezelő rendszer) beépített "
        "asszisztense vagy. A felhasználó admin felületen vagy telefonon, "
        "gyakran diktálva ír — a szöveg lehet pontatlan, ékezet nélküli.\n"
        f"Bejelentkezve: {actor.display_name} ({actor.role}). "
        f"Mai dátum: {datetime.now(UTC):%Y-%m-%d}.\n"
        "Szabályok:\n"
        "- A rendelkezésre álló eszközökkel dolgozz; ne találj ki adatot.\n"
        "- Írás jellegű művelet (jegy, számláló, feltöltés, munkalap) előtt ha "
        "bármi kétséges, kérdezz vissza röviden; ha egyértelmű, hajtsd végre.\n"
        "- Ha egy keresés több találatot ad, sorold fel és kérdezd meg, melyik.\n"
        "- Tömören, magyarul válaszolj (vagy a felhasználó nyelvén). "
        "A végrehajtott műveletet erősítsd meg egy mondatban.\n"
        "- Ha a felhasználó egy oldalt akar látni, használd a navigate eszközt."
    )


async def _anthropic_step(
    api_key: str, model: str, system: str, messages: list[dict], tools: list[dict]
) -> tuple[str, list[dict], Any]:
    """Egy modell-hívás. Vissza: (szöveg, tool_hívások, nyers assistant üzenet)."""
    from anthropic import AsyncAnthropic

    tool_defs = [
        {"name": t["name"], "description": t["description"], "input_schema": t["schema"]}
        for t in tools
    ]
    async with AsyncAnthropic(api_key=api_key) as client:
        res = await client.messages.create(
            model=model, max_tokens=1500, system=system,
            messages=messages, tools=tool_defs,
        )
    text = "".join(b.text for b in res.content if b.type == "text")
    calls = [
        {"id": b.id, "name": b.name, "args": dict(b.input or {})}
        for b in res.content
        if b.type == "tool_use"
    ]
    raw = [b.model_dump() for b in res.content]
    return text, calls, raw


async def _gemini_step(
    api_key: str, model: str, system: str, messages: list[dict], tools: list[dict]
) -> tuple[str, list[dict], Any]:
    import httpx

    decls = [
        {"name": t["name"], "description": t["description"], "parameters": t["schema"]}
        for t in tools
    ]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    async with httpx.AsyncClient(timeout=45) as client:
        res = await client.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": messages,
                "tools": [{"functionDeclarations": decls}],
                "generationConfig": {"maxOutputTokens": 1500},
            },
        )
        res.raise_for_status()
        data = res.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        return "", [], []
    text = "".join(p.get("text", "") for p in parts if "text" in p)
    calls = [
        {"id": p["functionCall"]["name"], "name": p["functionCall"]["name"],
         "args": dict(p["functionCall"].get("args") or {})}
        for p in parts
        if "functionCall" in p
    ]
    return text, calls, parts


async def run_chat(
    db: AsyncSession, actor: User, history: list[dict]
) -> dict:
    """A teljes asszisztens-hurok. history: [{role: user|assistant, content: str}].

    Vissza: {reply: str, events: [{kind, label, path?}]}.
    ValueError("ai_not_configured"), ha nincs AI-kulcs.
    """
    row = await ai_service.get_or_create_settings(db)
    provider = row.active_provider
    if provider not in ai_service.PROVIDERS:
        raise ValueError("ai_not_configured")
    api_key = decrypt_pii(
        row.anthropic_key_encrypted if provider == "anthropic" else row.gemini_key_encrypted
    )
    if not api_key:
        raise ValueError("ai_not_configured")
    model = row.anthropic_model if provider == "anthropic" else row.gemini_model

    matrix = await get_permission_matrix(db)
    tools = _available_tools(actor, matrix)
    system = _system_prompt(actor)
    events: list[dict] = []

    if provider == "anthropic":
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in history
            if m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()
        ]
        for _ in range(MAX_STEPS):
            text, calls, raw = await _anthropic_step(api_key, model, system, messages, tools)
            if not calls:
                return {"reply": text or "…", "events": events}
            messages.append({"role": "assistant", "content": raw})
            results = []
            for call in calls:
                result, event = await _execute_tool(db, actor, matrix, call["name"], call["args"])
                if event:
                    events.append(event)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": _json_short(result),
                })
            messages.append({"role": "user", "content": results})
        return {"reply": "Elértem a lépéskorlátot — próbáld kisebb lépésekben.", "events": events}

    # Gemini
    contents = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in history
        if m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()
    ]
    for _ in range(MAX_STEPS):
        text, calls, raw_parts = await _gemini_step(api_key, model, system, contents, tools)
        if not calls:
            return {"reply": text or "…", "events": events}
        contents.append({"role": "model", "parts": raw_parts})
        fn_parts = []
        for call in calls:
            result, event = await _execute_tool(db, actor, matrix, call["name"], call["args"])
            if event:
                events.append(event)
            fn_parts.append({
                "functionResponse": {"name": call["name"], "response": {"result": result}}
            })
        contents.append({"role": "user", "parts": fn_parts})
    return {"reply": "Elértem a lépéskorlátot — próbáld kisebb lépésekben.", "events": events}


def _json_short(value: dict, limit: int = 4000) -> str:
    import json

    out = json.dumps(value, ensure_ascii=False, default=str)
    return out[:limit]
