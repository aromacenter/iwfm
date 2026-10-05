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

from app.api.deps import get_current_user, get_permission_matrix, permissions_for, require_perm
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
    entries: list[CashEntryOut] = []


async def _cash_register(
    db: AsyncSession, user_id: uuid.UUID, user_name: str | None,
    date_from: date | None, date_to: date | None, with_entries: bool,
) -> CashRegisterOut:
    rev_q = select(
        sa_func.coalesce(sa_func.sum(Settlement.total_gross), 0.0)
    ).where(
        Settlement.settled_by_user_id == user_id,
        Settlement.payment_method == "cash",
    )
    rev_q = _range_filter(rev_q, Settlement.created_at, date_from, date_to)
    cash_revenue = float((await db.execute(rev_q)).scalar_one() or 0.0)

    exp_q = select(AgentExpense).where(AgentExpense.user_id == user_id)
    if date_from:
        exp_q = exp_q.where(AgentExpense.expense_date >= date_from)
    if date_to:
        exp_q = exp_q.where(AgentExpense.expense_date <= date_to)
    entries = (
        await db.execute(exp_q.order_by(AgentExpense.expense_date.desc()).limit(300))
    ).scalars().all()
    deposits = sum(e.amount_gross for e in entries if e.entry_type == "deposit")
    withdrawals = sum(e.amount_gross for e in entries if e.entry_type == "withdrawal")
    expenses = sum(
        e.amount_gross for e in entries if (e.entry_type or "expense") == "expense"
    )

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
        cash_revenue=_money(cash_revenue),
        deposits=_money(deposits), withdrawals=_money(withdrawals),
        expenses=_money(expenses),
        transfers_in=_money(t_in), transfers_out=_money(t_out),
        balance=_money(
            cash_revenue + deposits + t_in - t_out - withdrawals - expenses
        ),
        entries=out_entries,
    )


@router.get(
    "/cash/me", response_model=CashRegisterOut,
    dependencies=[Depends(require_module("cashbox"))],
)
async def my_cash_register(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """A bejelentkezett dolgozó saját kasszája, tételesen."""
    return await _cash_register(db, actor.id, actor.display_name, date_from, date_to, True)


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
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("invoicing")),
):
    """Központi kassza: minden dolgozó egyenlege + cégenkénti és fizetési
    módonkénti bontás. ``user_id`` megadásával egy kassza tételesen."""
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

    all_ids = set(rev_rows) | exp_users
    if user_id:
        try:
            all_ids = {uuid.UUID(user_id)}
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "user.not_found"})
    names: dict = {}
    if all_ids:
        for u in (
            await db.execute(select(User).where(User.id.in_(all_ids)))
        ).scalars():
            names[u.id] = u.display_name

    registers = []
    for uid in sorted(all_ids, key=lambda x: (names.get(x) or "").lower()):
        registers.append(
            await _cash_register(
                db, uid, names.get(uid), date_from, date_to,
                with_entries=bool(user_id),
            )
        )

    # Cégenkénti + fizetési módonkénti bontás (minden fizetési mód, a
    # készpénz külön oszlopban jól elhatárolva)
    st_q = select(
        Settlement.invoicing_company, Settlement.payment_method,
        sa_func.coalesce(sa_func.sum(Settlement.total_gross), 0.0),
        sa_func.count(),
    ).group_by(Settlement.invoicing_company, Settlement.payment_method)
    st_q = _range_filter(st_q, Settlement.created_at, date_from, date_to)
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
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """Bevétel/költség kimutatás: kategóriára (kávé, egyéb áru, szerviz,
    üzemeltetés), dolgozóra és gépre bontva, tetszőleges időszakra.
    invoicing VAGY agent_report jog kell hozzá."""
    matrix = await get_permission_matrix(db)
    perms = permissions_for(actor.role, matrix)
    if "invoicing" not in perms and "agent_report" not in perms:
        raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})

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
