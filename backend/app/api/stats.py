"""Kassza- és üzleti statisztika API.

Kassza (cashbox modul): dolgozónkénti készpénz-egyenleg — készpénzes
elszámolás-bevételek + betétek − kivétek − költségek; központi (összesített)
nézet cégenkénti bontással. A dolgozó a sajátját látja, a központi nézethez
invoicing jog kell.

Statisztika (stats modul): bevétel/költség bontás kategóriára (kávé, egyéb
áru, szerviz, üzemeltetés), dolgozóra és gépre, szabad időszakra.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_permission_matrix, is_manager, permissions_for, require_perm
from app.db import get_db
from app.models import (
    AgentExpense,
    Product,
    Settlement,
    SettlementLine,
    SettlementMachine,
    User,
    Worksheet,
)
from app.services.wfm.license import require_module

router = APIRouter()


def _money(v: float) -> float:
    # Kassza/statisztika: 1 Ft-ra kerekítve — tizedes nem kell a felületen.
    return float(round(v or 0.0))


def _range_filter(q, column, date_from: date | None, date_to: date | None):
    """created_at (datetime) szűrés zárt napokra."""
    if date_from:
        q = q.where(column >= datetime.combine(date_from, time.min))
    if date_to:
        q = q.where(column <= datetime.combine(date_to, time.max))
    return q


# ─── Kassza ──────────────────────────────────────────────────────────────────


class CashEntryOut(BaseModel):
    id: str
    expense_date: date
    amount_gross: float
    entry_type: str
    note: str | None
    supplier: str | None
    receipt_no: str | None
    created_by_name: str | None = None


class ContractorLedgerOut(BaseModel):
    """Külsős szerelő folyószámlája (a szerelő SAJÁT nézetéhez): az elhozott
    munkalapok visszaigazolt díjai + saját költései − kifizetések."""

    fee_total: float
    costs: float
    payouts: float
    balance: float
    worksheets: int


class CashRegisterOut(BaseModel):
    user_id: str
    user_name: str | None
    cash_revenue: float  # készpénzes elszámolás-bevétel (bruttó)
    deposits: float
    withdrawals: float
    expenses: float
    transfers_in: float = 0.0  # elfogadott bejövő pénz-átadások
    transfers_out: float = 0.0
    balance: float
    contractor: ContractorLedgerOut | None = None  # szerelő-folyószámla (ha van)
    entries: list[CashEntryOut] = []


# A kassza 2026-10-12-én tiszta lappal újraindult (30ea42b2): az alap-nézet
# és az egyenleg CSAK az ettől a naptól rögzített tételekből számol. A
# korábbi (migrált + próbaidőszakos) tételek az admin Archívum nézetében
# maradnak visszakereshetők.
CASH_EPOCH = date(2026, 10, 12)


async def _cash_sums(
    db: AsyncSession, user_id: uuid.UUID,
    date_from: date | None, date_to: date | None, user_role: str | None,
) -> dict:
    """Nyers kassza-összegek egy időszakra (bevétel, betét/kivét/költség,
    elfogadott átadások)."""
    rev_q = select(
        sa_func.coalesce(sa_func.sum(Settlement.total_gross), 0.0)
    ).where(
        Settlement.settled_by_user_id == user_id,
        Settlement.payment_method == "cash",
    )
    rev_q = _range_filter(rev_q, Settlement.created_at, date_from, date_to)
    cash_revenue = float((await db.execute(rev_q)).scalar_one() or 0.0)

    # FIGYELEM: a GROUP BY kötelező — SQLite elnézi nélküle, a Postgres NEM
    # (GroupingError → 500 a teljes kasszára; 3398b0d7 blokkoló oka volt).
    sum_q = select(
        AgentExpense.entry_type,
        sa_func.coalesce(sa_func.sum(AgentExpense.amount_gross), 0.0),
    ).where(AgentExpense.user_id == user_id).group_by(AgentExpense.entry_type)
    if date_from:
        sum_q = sum_q.where(AgentExpense.expense_date >= date_from)
    if date_to:
        sum_q = sum_q.where(AgentExpense.expense_date <= date_to)
    sums = {(t or "expense"): float(v or 0.0) for t, v in (await db.execute(sum_q)).all()}

    # Elfogadott pénz-átadások: bejövő +, kimenő −
    from app.models import CashTransfer

    t_in = float((await db.execute(
        _range_filter(
            select(sa_func.coalesce(sa_func.sum(CashTransfer.amount), 0.0)).where(
                CashTransfer.to_user_id == user_id, CashTransfer.status == "accepted",
            ),
            CashTransfer.decided_at, date_from, date_to,
        )
    )).scalar_one() or 0.0)
    t_out = float((await db.execute(
        _range_filter(
            select(sa_func.coalesce(sa_func.sum(CashTransfer.amount), 0.0)).where(
                CashTransfer.from_user_id == user_id, CashTransfer.status == "accepted",
            ),
            CashTransfer.decided_at, date_from, date_to,
        )
    )).scalar_one() or 0.0)
    # ADMIN címzettnél az elfogadott pénz-leadás a VÉGÁLLOMÁS (b5a36526): a
    # pénz "megérkezett a céghez", az admin kasszáját nem növeli — így nem is
    # duplázódhat a cég forgalmában. Managernél marad az átadás-logika.
    if user_role == "admin":
        t_in = 0.0
    return {
        "revenue": cash_revenue,
        "deposits": sums.get("deposit", 0.0),
        "withdrawals": sums.get("withdrawal", 0.0),
        "expenses": sums.get("expense", 0.0),
        "t_in": t_in,
        "t_out": t_out,
    }


def _balance_of(s: dict) -> float:
    return (s["revenue"] + s["deposits"] + s["t_in"]
            - s["t_out"] - s["withdrawals"] - s["expenses"])


async def _cash_register(
    db: AsyncSession, user_id: uuid.UUID, user_name: str | None,
    date_from: date | None, date_to: date | None, with_entries: bool,
    user_role: str | None = None,
    archive: bool = False,
) -> CashRegisterOut:
    # Alapnézet: minden az epoch-tól indul; archívum: minden az epoch ELŐTT.
    if archive:
        arch_end = CASH_EPOCH - timedelta(days=1)
        eff_from = date_from
        eff_to = min(date_to, arch_end) if date_to else arch_end
    else:
        eff_from = max(date_from, CASH_EPOCH) if date_from else CASH_EPOCH
        eff_to = date_to

    sums = await _cash_sums(db, user_id, eff_from, eff_to, user_role)
    # Az EGYENLEG mindig a teljes göngyölt időszak (epoch → ma, archívumban a
    # teljes régi történet) — a gyors-szűrők csak a részösszegeket és a
    # tétel-listát szűkítik, az egyenleget nem írhatják át (b5a36526 reopen).
    full_from = None if archive else CASH_EPOCH
    full_to = (CASH_EPOCH - timedelta(days=1)) if archive else None
    if (eff_from, eff_to) == (full_from, full_to):
        bal_sums = sums
    else:
        bal_sums = await _cash_sums(db, user_id, full_from, full_to, user_role)

    exp_q = select(AgentExpense).where(AgentExpense.user_id == user_id)
    if eff_from:
        exp_q = exp_q.where(AgentExpense.expense_date >= eff_from)
    if eff_to:
        exp_q = exp_q.where(AgentExpense.expense_date <= eff_to)
    entries = (
        await db.execute(exp_q.order_by(AgentExpense.expense_date.desc()).limit(300))
    ).scalars().all()
    # Az összegek SQL-aggregátumból jönnek — a tétel-lista 300-as limitje
    # (megjelenítés) nem torzíthatja az egyenleget (import utáni nagy történet!).

    out_entries: list[CashEntryOut] = []
    if with_entries:
        creator_ids = {e.created_by for e in entries if e.created_by}
        names: dict = {}
        if creator_ids:
            for u in (
                await db.execute(select(User).where(User.id.in_(creator_ids)))
            ).scalars():
                names[u.id] = u.display_name
        out_entries = [
            CashEntryOut(
                id=str(e.id), expense_date=e.expense_date,
                amount_gross=e.amount_gross,
                entry_type=e.entry_type or "expense",
                note=e.note, supplier=e.supplier, receipt_no=e.receipt_no,
                created_by_name=names.get(e.created_by),
            )
            for e in entries
        ]
    return CashRegisterOut(
        user_id=str(user_id), user_name=user_name,
        cash_revenue=_money(sums["revenue"]),
        deposits=_money(sums["deposits"]), withdrawals=_money(sums["withdrawals"]),
        expenses=_money(sums["expenses"]),
        transfers_in=_money(sums["t_in"]), transfers_out=_money(sums["t_out"]),
        balance=_money(_balance_of(bal_sums)),
        entries=out_entries,
    )


@router.get(
    "/cash/me", response_model=CashRegisterOut,
    dependencies=[Depends(require_module("cashbox"))],
)
async def my_cash_register(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    archive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A bejelentkezett dolgozó saját kasszája, tételesen. Külsős szerelőnél
    a szerelő-folyószámla is (járó díjak + költései − kifizetések) — így a
    szerelő a saját kasszáján látja, mennyivel tartozunk neki (53af6761).
    ``archive=true`` (csak admin): a 2026-10-12-i újraindítás ELŐTTI tételek."""
    if archive and actor.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
    out = await _cash_register(
        db, actor.id, actor.display_name, date_from, date_to, True,
        user_role=actor.role, archive=archive,
    )
    out.contractor = await _contractor_ledger(db, actor.id)
    return out


async def _contractor_ledger(db: AsyncSession, user_id: uuid.UUID) -> ContractorLedgerOut | None:
    """A userhez tartozó dolgozó szerelő-folyószámlája (időszűrés nélkül —
    ez göngyölt egyenleg), ha van KSZ-munkalapja vagy folyószámla-tétele."""
    from app.models import Employee, Task, TechLedger

    emp_id = (
        await db.execute(select(Employee.id).where(Employee.user_id == user_id))
    ).scalars().first()
    if emp_id is None:
        return None
    from app.api.tasks import _service_fee_total

    ws_rows = (
        await db.execute(
            select(Worksheet)
            .join(Task, Task.id == Worksheet.task_id)
            .where(
                Task.employee_id == emp_id,
                Worksheet.external_service.is_(True),
                Worksheet.picked_up_at.is_not(None),
            )
        )
    ).scalars().all()
    tl_rows = (
        await db.execute(
            select(TechLedger.kind, sa_func.coalesce(sa_func.sum(TechLedger.amount), 0.0))
            .where(TechLedger.employee_id == emp_id)
            .group_by(TechLedger.kind)
        )
    ).all()
    if not ws_rows and not tl_rows:
        return None
    sums = {k: float(v or 0.0) for k, v in tl_rows}
    fee_total = sum(_service_fee_total(ws) for ws in ws_rows)
    costs = sums.get("cost", 0.0)
    payouts = sums.get("payout", 0.0)
    return ContractorLedgerOut(
        fee_total=_money(fee_total), costs=_money(costs), payouts=_money(payouts),
        balance=_money(fee_total + costs - payouts), worksheets=len(ws_rows),
    )


class MapPartnerOut(BaseModel):
    id: str
    name: str
    city: str | None
    zip: str | None
    lat: float | None
    lng: float | None
    machines: int  # FIZIKAI gépek (0e8c6908)
    counters: int = 0  # számláló-sorok ("régi gépszám", érdekesség)
    revenue: float  # összes elszámolás-bevétel (bruttó)
    agent_user_id: str | None = None  # felelős képviselő (szűréshez)
    contracted: bool = True  # ma érvényes szerződése van (d650f1d5: a gép nélküliek is látszanak)


@router.get(
    "/map", response_model=list[MapPartnerOut],
    dependencies=[Depends(require_module("stats"))],
)
async def stats_map(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("agent_report")),
):
    """Térkép-nézet adatai (8f78ba71): partnerenként a kihelyezett gépek száma
    és az összes bevétel — a kliens városonként összesíti és rajzolja ki.
    CSAK az aktív, MA ÉRVÉNYES szerződésű, géppel rendelkező partnerek
    (8908b10e) — a régi/ügyfél-státuszúak nem."""
    from app.models import Asset, Partner, PartnerContract

    today_ = date.today()
    contracted_ids = set(
        (
            await db.execute(
                select(PartnerContract.partner_id).where(
                    PartnerContract.valid_from <= today_,
                    (PartnerContract.valid_to.is_(None))
                    | (PartnerContract.valid_to >= today_),
                )
            )
        ).scalars().all()
    )

    # Fizikai gépek + számláló-sorok partnerenként (0e8c6908)
    from app.api.inventory import physical_machine_counts

    mach_rows = (
        await db.execute(
            select(Asset.partner_id, Asset.barcode)
            .where(Asset.status == "deployed", Asset.partner_id.is_not(None))
        )
    ).all()
    mach, counter_rows = physical_machine_counts(mach_rows)
    # A bevétel itt is az újraindítástól számol (30ea42b2) — a régi (migrált)
    # forgalom csak az admin statisztika-archívumban él.
    rev_q = select(Settlement.partner_id, sa_func.sum(Settlement.total_gross)).group_by(
        Settlement.partner_id
    )
    rev_q = _range_filter(rev_q, Settlement.created_at, CASH_EPOCH, None)
    rev = {pid: float(v or 0.0) for pid, v in (await db.execute(rev_q)).all()}
    out: list[MapPartnerOut] = []
    for p in (
        await db.execute(select(Partner).where(Partner.is_active.is_(True)))
    ).scalars().all():
        machines = mach.get(p.id, 0)
        revenue = rev.get(p.id, 0.0)
        # d650f1d5: MINDEN aktív partner a térképre kerül; a gép nélküli /
        # szerződés nélküli jelölten (a kliens szürkén rajzolja, szűrhető)
        out.append(MapPartnerOut(
            id=str(p.id), name=p.name, city=p.address_city, zip=p.address_zip,
            lat=p.lat, lng=p.lng, machines=machines,
            counters=int(counter_rows.get(p.id, 0)), revenue=_money(revenue),
            agent_user_id=str(p.agent_user_id) if p.agent_user_id else None,
            contracted=p.id in contracted_ids,
        ))
    return out


@router.post(
    "/map/geocode-missing",
    dependencies=[Depends(require_module("stats"))],
)
async def map_geocode_missing(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("agent_report")),
):
    """d650f1d5: minden aktív, koordináta nélküli partner geokódolása a
    háttérben, sorban (a Nominatim 1 kérés/mp szabálya miatt lassú: ~1,2 s/partner)."""
    import asyncio

    from app.models import Partner
    from app.services.wfm.geocode import enabled, geocode_partner_background

    ids = (
        await db.execute(
            select(Partner.id).where(Partner.is_active.is_(True), Partner.lat.is_(None))
        )
    ).scalars().all()

    async def _run(partner_ids: list) -> None:
        for pid in partner_ids:
            await geocode_partner_background(pid)
            await asyncio.sleep(1.2)

    if ids and enabled():
        asyncio.get_running_loop().create_task(_run(list(ids)))
    return {"queued": len(ids) if enabled() else 0, "missing": len(ids)}


@router.get("/machine-history", dependencies=[Depends(require_module("stats"))])
async def machine_history(
    barcode: str = Query(min_length=3, max_length=64),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """Egy gép TELJES elszámolás- és szerviz-története (5404e465) — a
    statisztika gép-soraira kattintva nyílik. Szándékosan nem epoch-olt:
    ez a gép életrajza, nem forgalmi statisztika."""
    matrix = await get_permission_matrix(db)
    perms = permissions_for(actor.role, matrix)
    if "invoicing" not in perms and "agent_report" not in perms:
        raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
    from app.models import Asset, Partner, ServiceTicket

    asset = (
        await db.execute(select(Asset).where(Asset.barcode == barcode.strip()))
    ).scalar_one_or_none()

    st_rows = (
        await db.execute(
            select(SettlementMachine, Settlement.created_at, Partner.name)
            .join(Settlement, Settlement.id == SettlementMachine.settlement_id)
            .outerjoin(Partner, Partner.id == Settlement.partner_id)
            .where(SettlementMachine.barcode == barcode.strip())
            .order_by(Settlement.created_at.desc())
            .limit(100)
        )
    ).all()
    settlements = [
        {
            "date": created_at,
            "partner": pname,
            "prev_counter": sm.prev_counter,
            "new_counter": sm.new_counter,
            "portions": sm.portions_billed,
            "net": _money(sm.amount_net or 0.0),
        }
        for sm, created_at, pname in st_rows
    ]
    service: list[dict] = []
    if asset is not None:
        tickets = (
            await db.execute(
                select(ServiceTicket)
                .where(ServiceTicket.asset_id == asset.id)
                .order_by(ServiceTicket.created_at.desc())
                .limit(50)
            )
        ).scalars().all()
        service = [
            {
                "ticket_no": tk.ticket_no, "title": tk.title, "status": tk.status,
                "created_at": tk.created_at, "partner": tk.partner_label,
            }
            for tk in tickets
        ]
    return {
        "barcode": barcode.strip(),
        "name": asset.name if asset else None,
        "status": asset.status if asset else None,
        "settlements": settlements,
        "service": service,
    }


@router.get(
    "/cash/suppliers", response_model=list[str],
    dependencies=[Depends(require_module("cashbox"))],
)
async def cash_suppliers(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """A korábban rögzített beszállító-nevek (költés-űrlap önkitöltéséhez) —
    a mező szabad szavas marad, ez csak javaslat-lista (68c1bcf4)."""
    from app.models import TechLedger

    names: set[str] = set()
    for (s,) in (
        await db.execute(select(AgentExpense.supplier).distinct().where(AgentExpense.supplier.is_not(None)))
    ).all():
        if s and s.strip():
            names.add(s.strip())
    for (s,) in (
        await db.execute(select(TechLedger.supplier).distinct().where(TechLedger.supplier.is_not(None)))
    ).all():
        if s and s.strip():
            names.add(s.strip())
    return sorted(names, key=str.lower)[:300]


class CashOverviewOut(BaseModel):
    registers: list[CashRegisterOut]
    total_balance: float
    # cégenkénti bontás: készpénz + többi fizetési mód (jól elhatárolva)
    by_company: dict
    by_payment: dict


@router.get(
    "/cash/overview", response_model=CashOverviewOut,
    dependencies=[Depends(require_module("cashbox"))],
)
async def cash_overview(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    user_id: str | None = Query(default=None),  # egy dolgozó tételesen
    archive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("invoicing")),
):
    """Központi kassza: minden dolgozó egyenlege + cégenkénti és fizetési
    módonkénti bontás. ``user_id`` megadásával egy kassza tételesen.
    ``archive=true`` (csak admin): a kassza-újraindítás előtti világ (30ea42b2)."""
    if archive:
        if actor.role != "admin":
            raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
        # Az archívum a teljes régi időszak — a gyors-szűrők itt nem élnek.
        date_from = None
        date_to = CASH_EPOCH - timedelta(days=1)
    # Minden user, akinek készpénz-bevétele VAGY kassza-tétele van
    rev_rows_q = select(
        Settlement.settled_by_user_id,
        sa_func.coalesce(sa_func.sum(Settlement.total_gross), 0.0),
    ).where(
        Settlement.payment_method == "cash",
        Settlement.settled_by_user_id.is_not(None),
    ).group_by(Settlement.settled_by_user_id)
    rev_rows_q = _range_filter(rev_rows_q, Settlement.created_at, date_from, date_to)
    rev_rows = dict((await db.execute(rev_rows_q)).all())

    exp_user_q = select(AgentExpense.user_id).distinct()
    if date_from:
        exp_user_q = exp_user_q.where(AgentExpense.expense_date >= date_from)
    if date_to:
        exp_user_q = exp_user_q.where(AgentExpense.expense_date <= date_to)
    exp_users = set((await db.execute(exp_user_q)).scalars().all())

    # MINDEN aktív dolgozó kasszája listázódik — üres kasszába is lehet
    # betenni/kivenni (c1e08c67: eddig csak az jelent meg, akinek már volt
    # bevétele vagy tétele).
    active_users = set(
        (await db.execute(select(User.id).where(User.is_active.is_(True)))).scalars().all()
    )
    all_ids = set(rev_rows) | exp_users | active_users
    # Képviselő (és minden nem admin/manager): CSAK a saját kasszája (e16fec86)
    if not is_manager(actor):
        all_ids = {actor.id}
        user_id = None
    if user_id:
        try:
            all_ids = {uuid.UUID(user_id)}
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "user.not_found"})
    names: dict = {}
    roles: dict = {}
    if all_ids:
        for u in (
            await db.execute(select(User).where(User.id.in_(all_ids)))
        ).scalars():
            names[u.id] = u.display_name
            roles[u.id] = u.role

    registers = []
    for uid in sorted(all_ids, key=lambda x: (names.get(x) or "").lower()):
        registers.append(
            await _cash_register(
                db, uid, names.get(uid), date_from if not archive else None, date_to,
                with_entries=bool(user_id) or archive, user_role=roles.get(uid),
                archive=archive,
            )
        )

    # Cégenkénti + fizetési módonkénti bontás (minden fizetési mód, a
    # készpénz külön oszlopban jól elhatárolva)
    st_q = select(
        Settlement.invoicing_company, Settlement.payment_method,
        sa_func.coalesce(sa_func.sum(Settlement.total_gross), 0.0),
        sa_func.count(),
    ).group_by(Settlement.invoicing_company, Settlement.payment_method)
    # A cégenkénti/fizetési módos bontás is az újraindított kasszát követi.
    st_from = date_from if archive else max(date_from or CASH_EPOCH, CASH_EPOCH)
    st_q = _range_filter(st_q, Settlement.created_at, st_from, date_to)
    by_company: dict = {}
    by_payment: dict = {}
    for comp, pm, gross, cnt in (await db.execute(st_q)).all():
        ckey = comp if comp in ("xp", "pc") else "none"
        c = by_company.setdefault(ckey, {"cash": 0.0, "other": 0.0, "count": 0})
        c["count"] += int(cnt)
        c["cash" if pm == "cash" else "other"] += float(gross or 0.0)
        p = by_payment.setdefault(pm, {"gross": 0.0, "count": 0})
        p["gross"] += float(gross or 0.0)
        p["count"] += int(cnt)
    for c in by_company.values():
        c["cash"] = _money(c["cash"])
        c["other"] = _money(c["other"])
    for p in by_payment.values():
        p["gross"] = _money(p["gross"])

    return CashOverviewOut(
        registers=registers,
        total_balance=_money(sum(r.balance for r in registers)),
        by_company=by_company,
        by_payment=by_payment,
    )


# ─── Üzleti statisztika ──────────────────────────────────────────────────────


@router.get(
    "/business",
    dependencies=[Depends(require_module("stats"))],
)
async def business_stats(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    archive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """Bevétel/költség kimutatás: kategóriára (kávé, egyéb áru, szerviz,
    üzemeltetés), dolgozóra és gépre bontva, tetszőleges időszakra.
    invoicing VAGY agent_report jog kell hozzá.
    A statisztika is a 2026-10-12-i újraindítástól számol (30ea42b2) —
    ``archive=true`` (csak admin) a régi világot mutatja."""
    matrix = await get_permission_matrix(db)
    perms = permissions_for(actor.role, matrix)
    if "invoicing" not in perms and "agent_report" not in perms:
        raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
    if archive:
        if actor.role != "admin":
            raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
        date_from = None
        date_to = min(date_to, CASH_EPOCH - timedelta(days=1)) if date_to else CASH_EPOCH - timedelta(days=1)
    else:
        date_from = max(date_from or CASH_EPOCH, CASH_EPOCH)

    # Elszámolás-sorok termékkel: kávé vs egyéb áru vs üzemeltetés (termék
    # nélküli sorok: bérleti díj, minimum-különbözet, készlethiány).
    line_q = (
        select(SettlementLine, Product.is_consignment)
        .join(Settlement, Settlement.id == SettlementLine.settlement_id)
        .outerjoin(Product, Product.id == SettlementLine.product_id)
    )
    line_q = _range_filter(line_q, Settlement.created_at, date_from, date_to)
    categories = {
        "coffee": {"net": 0.0, "count": 0},
        "goods": {"net": 0.0, "count": 0},
        "operations": {"net": 0.0, "count": 0},  # bérleti díj, minimum, hiány
        "service": {"net": 0.0, "count": 0},
    }
    for line, is_consignment in (await db.execute(line_q)).all():
        if line.product_id is None or line.product_name.startswith("Készlethiány"):
            cat = "operations"
        elif is_consignment:
            cat = "coffee"
        else:
            cat = "goods"
        categories[cat]["net"] += line.amount_net
        categories[cat]["count"] += 1

    # Szerviz-bevétel: átadott munkalapok (kedvezmény nélkül)
    ws_q = select(
        sa_func.coalesce(sa_func.sum(Worksheet.handover_total_net), 0.0),
        sa_func.count(),
    ).where(Worksheet.handed_over_at.is_not(None))
    ws_q = _range_filter(ws_q, Worksheet.handed_over_at, date_from, date_to)
    ws_net, ws_count = (await db.execute(ws_q)).one()
    categories["service"]["net"] = float(ws_net or 0.0)
    categories["service"]["count"] = int(ws_count or 0)
    for c in categories.values():
        c["net"] = _money(c["net"])

    # Dolgozónként (elszámolások)
    emp_q = select(
        Settlement.settled_by_name,
        sa_func.coalesce(sa_func.sum(Settlement.total_net), 0.0),
        sa_func.coalesce(sa_func.sum(Settlement.total_gross), 0.0),
        sa_func.count(),
    ).group_by(Settlement.settled_by_name)
    emp_q = _range_filter(emp_q, Settlement.created_at, date_from, date_to)
    by_employee = [
        {"name": n, "net": _money(float(net or 0)), "gross": _money(float(g or 0)),
         "count": int(c)}
        for n, net, g, c in (await db.execute(emp_q)).all()
    ]
    by_employee.sort(key=lambda x: -x["net"])

    # Gépenként (gép-soros elszámolások)
    m_q = (
        select(
            SettlementMachine.barcode,
            sa_func.max(SettlementMachine.asset_name),
            sa_func.coalesce(sa_func.sum(SettlementMachine.amount_net), 0.0),
            sa_func.coalesce(sa_func.sum(SettlementMachine.portions_billed), 0.0),
            sa_func.count(),
        )
        .join(Settlement, Settlement.id == SettlementMachine.settlement_id)
        .group_by(SettlementMachine.barcode)
    )
    m_q = _range_filter(m_q, Settlement.created_at, date_from, date_to)
    by_machine = [
        {"barcode": bc, "name": nm, "net": _money(float(net or 0)),
         "portions": round(float(p or 0)), "count": int(c)}
        for bc, nm, net, p, c in (await db.execute(m_q)).all()
    ]
    by_machine.sort(key=lambda x: -x["net"])

    # Költségek dolgozónként (kassza-tételek)
    cost_q = select(
        AgentExpense.user_id,
        AgentExpense.entry_type,
        sa_func.coalesce(sa_func.sum(AgentExpense.amount_gross), 0.0),
    ).group_by(AgentExpense.user_id, AgentExpense.entry_type)
    if date_from:
        cost_q = cost_q.where(AgentExpense.expense_date >= date_from)
    if date_to:
        cost_q = cost_q.where(AgentExpense.expense_date <= date_to)
    cost_rows = (await db.execute(cost_q)).all()
    cost_user_ids = {r[0] for r in cost_rows}
    cnames: dict = {}
    if cost_user_ids:
        for u in (
            await db.execute(select(User).where(User.id.in_(cost_user_ids)))
        ).scalars():
            cnames[u.id] = u.display_name
    costs_by_user: dict = {}
    for uid, et, total in cost_rows:
        row = costs_by_user.setdefault(
            str(uid), {"name": cnames.get(uid), "expense": 0.0, "deposit": 0.0,
                       "withdrawal": 0.0},
        )
        row[et or "expense"] = _money(float(total or 0.0))
    expenses_total = sum(r["expense"] for r in costs_by_user.values())

    revenue_total = sum(c["net"] for c in categories.values())
    return {
        "categories": categories,
        "by_employee": by_employee[:100],
        "by_machine": by_machine[:200],
        "costs_by_user": list(costs_by_user.values()),
        "expenses_total": _money(expenses_total),
        "revenue_total_net": _money(revenue_total),
    }


@router.get("/business/drill", dependencies=[Depends(require_module("stats"))])
async def business_drill(
    kind: str = Query(pattern="^(revenue|expense)$"),
    name: str = Query(min_length=1, max_length=256),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    archive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A statisztika dolgozónkénti összegei mögötti tételek (84452fba):
    kind=revenue → a dolgozó elszámolásai; kind=expense → a kassza-tételei."""
    matrix = await get_permission_matrix(db)
    perms = permissions_for(actor.role, matrix)
    if "invoicing" not in perms and "agent_report" not in perms:
        raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
    # A drill is az újraindítástól számol (30ea42b2) — archívumban a régi világ.
    if archive:
        if actor.role != "admin":
            raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
        date_from = None
        date_to = min(date_to, CASH_EPOCH - timedelta(days=1)) if date_to else CASH_EPOCH - timedelta(days=1)
    else:
        date_from = max(date_from or CASH_EPOCH, CASH_EPOCH)

    if kind == "revenue":
        from app.models import Partner

        q = (
            select(Settlement, Partner.name)
            .join(Partner, Partner.id == Settlement.partner_id)
            .where(Settlement.settled_by_name == name)
            .order_by(Settlement.created_at.desc())
            .limit(300)
        )
        q = _range_filter(q, Settlement.created_at, date_from, date_to)
        return [
            {
                "id": str(s.id),
                "created_at": s.created_at.isoformat(),
                "partner_name": pname,
                "total_net": _money(s.total_net),
                "total_gross": _money(s.total_gross),
                "payment_method": s.payment_method,
                "invoiced": s.invoiced,
            }
            for s, pname in (await db.execute(q)).all()
        ]

    uid = (
        await db.execute(select(User.id).where(User.display_name == name))
    ).scalars().first()
    if uid is None:
        return []
    q = (
        select(AgentExpense)
        .where(AgentExpense.user_id == uid)
        .order_by(AgentExpense.expense_date.desc())
        .limit(300)
    )
    if date_from:
        q = q.where(AgentExpense.expense_date >= date_from)
    if date_to:
        q = q.where(AgentExpense.expense_date <= date_to)
    return [
        {
            "expense_date": e.expense_date.isoformat(),
            "amount_gross": _money(e.amount_gross),
            "entry_type": e.entry_type or "expense",
            "note": e.note,
            "supplier": e.supplier,
            "receipt_no": e.receipt_no,
        }
        for e in (await db.execute(q)).scalars().all()
    ]


@router.get(
    "/cash/central-recipients",
    dependencies=[Depends(require_module("cashbox"))],
)
async def central_recipients(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A "Pénz átadása" címzettjei: admin, manager ÉS képviselő (üzletkötő)
    felhasználók — a saját magunkat kivéve (2817884b: képviselő képviselőnek is
    adhat át); az adminnál landolt pénz leadottnak számít (b5a36526)."""
    rows = (
        await db.execute(
            select(User).where(User.role.in_(("admin", "manager", "uzletkoto")), User.is_active.is_(True), User.id != actor.id)
        )
    ).scalars().all()
    order = {"admin": 0, "manager": 1, "uzletkoto": 2}
    return [
        {"user_id": str(u.id), "name": u.display_name, "role": u.role}
        for u in sorted(rows, key=lambda x: (order.get(x.role, 9), x.display_name or ""))
    ]


# ─── Pénz-átadás képviselők között (elfogadással) ────────────────────────────


class TransferBody(BaseModel):
    to_user_id: str
    amount: float
    note: str | None = None


class TransferOut(BaseModel):
    id: str
    from_user_id: str
    from_name: str | None
    to_user_id: str
    to_name: str | None
    amount: float
    note: str | None
    status: str
    created_at: datetime


async def _transfer_out(db: AsyncSession, rows) -> list[TransferOut]:
    from app.models import CashTransfer  # noqa: F401

    ids = {r.from_user_id for r in rows} | {r.to_user_id for r in rows}
    names: dict = {}
    if ids:
        for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars():
            names[u.id] = u.display_name
    return [
        TransferOut(
            id=str(r.id), from_user_id=str(r.from_user_id),
            from_name=names.get(r.from_user_id),
            to_user_id=str(r.to_user_id), to_name=names.get(r.to_user_id),
            amount=_money(r.amount), note=r.note, status=r.status,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post(
    "/cash/transfer", response_model=TransferOut,
    dependencies=[Depends(require_module("cashbox"))], status_code=201,
)
async def create_transfer(
    body: TransferBody,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """Készpénz átadása másik képviselőnek — a címzett elfogadásáig függő."""
    from app.models import CashTransfer

    if body.amount <= 0 or body.amount > 10_000_000:
        raise HTTPException(status_code=422, detail={"code": "cash.bad_amount"})
    try:
        to_id = uuid.UUID(body.to_user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "user.not_found"})
    if to_id == actor.id:
        raise HTTPException(status_code=422, detail={"code": "cash.self_transfer"})
    target = (await db.execute(select(User).where(User.id == to_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "user.not_found"})
    t = CashTransfer(
        from_user_id=actor.id, to_user_id=to_id,
        amount=float(round(body.amount)), note=(body.note or "").strip()[:512] or None,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return (await _transfer_out(db, [t]))[0]


@router.get(
    "/cash/transfers", response_model=list[TransferOut],
    dependencies=[Depends(require_module("cashbox"))],
)
async def list_transfers(
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A rám váró (függő) és a legutóbbi saját átadásaim."""
    from app.models import CashTransfer

    rows = (
        await db.execute(
            select(CashTransfer)
            .where(
                (CashTransfer.to_user_id == actor.id)
                | (CashTransfer.from_user_id == actor.id)
            )
            .order_by(CashTransfer.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return await _transfer_out(db, rows)


@router.post(
    "/cash/transfers/{transfer_id}/decide", response_model=TransferOut,
    dependencies=[Depends(require_module("cashbox"))],
)
async def decide_transfer(
    transfer_id: str,
    accept: bool = Query(...),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A címzett elfogadja vagy elutasítja a pénz-átadást — elfogadáskor az
    összeg az ő kasszájába kerül, az átadóéból levonódik."""
    from app.models import CashTransfer

    try:
        tid = uuid.UUID(transfer_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "cash.transfer_not_found"})
    t = (
        await db.execute(select(CashTransfer).where(CashTransfer.id == tid))
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "cash.transfer_not_found"})
    if t.to_user_id != actor.id:
        raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
    if t.status != "pending":
        raise HTTPException(status_code=422, detail={"code": "cash.transfer_decided"})
    t.status = "accepted" if accept else "declined"
    t.decided_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(t)
    return (await _transfer_out(db, [t]))[0]
