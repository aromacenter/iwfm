"""Áremelés / szerződés-indexálás (184. kör).

Egy áremelés: partnerek köre + százalék VAGY fix összeg + kerekítés + hatály.
Előnézet: minden érintett ár (partner-termékár, szerződéses minimum alatti
adag-/kg-ár, sávos adagárak, fix bérleti díj, gép számlálónkénti adagár)
régi → új. Ütemezve a hatály napján az óránkénti kör alkalmazza; a sorok
pillanatképe a visszavonást is lehetővé teszi (csak ami azóta nem változott).
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset, AuditEvent, Partner, PartnerContract, PartnerPrice, PriceIndexation, Product

logger = logging.getLogger(__name__)

TARGETS = ("partner_price", "below_min", "below_min_kg", "tier", "rent_fee", "counter_price")
TARGET_LABELS = {
    "partner_price": "partner-termékár (Ft/adag)",
    "below_min": "minimum alatti adagár (Ft/adag)",
    "below_min_kg": "minimum alatti ár (Ft/kg)",
    "tier": "sávos adagár (Ft/adag)",
    "rent_fee": "fix bérleti díj (Ft/hó)",
    "counter_price": "gép számlálónkénti adagár (Ft/adag)",
}


def new_price(old: float, percent: float | None, amount: float | None, round_to: int = 1) -> float:
    """Új ár: százalékos vagy fix emelés, felfelé kerekítve a megadott egységre."""
    val = float(old)
    if percent:
        val = val * (1 + float(percent) / 100)
    if amount:
        val = val + float(amount)
    step = max(1, int(round_to or 1))
    val = math.ceil(round(val, 6) / step) * step if step > 1 else round(val, 2)
    return float(val)


def _uuid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        return None


async def _partners(db: AsyncSession, partner_ids: list[str] | None) -> list[Partner]:
    q = select(Partner).where(Partner.is_active.is_(True)).order_by(Partner.name)
    if partner_ids:
        ids = [u for u in (_uuid(x) for x in partner_ids) if u is not None]
        q = select(Partner).where(Partner.id.in_(ids)).order_by(Partner.name)
    return list((await db.execute(q)).scalars().all())


async def compute_rows(
    db: AsyncSession, *, partner_ids: list[str] | None, targets: list[str],
    percent: float | None, amount: float | None, round_to: int, effective_from: date,
) -> list[dict]:
    """Az érintett árak listája: {partner_id, partner, kind, ref, label, old, new}."""
    want = {t for t in targets if t in TARGETS}
    rows: list[dict] = []
    partners = await _partners(db, partner_ids)
    if not partners:
        return rows
    pids = [p.id for p in partners]
    names = {p.id: p.name for p in partners}

    def add(pid, kind, ref, label, old):
        if old is None or float(old) <= 0:
            return
        new = new_price(float(old), percent, amount, round_to)
        if new == float(old):
            return
        rows.append({"partner_id": str(pid), "partner": names[pid], "kind": kind, "ref": ref,
                     "label": label, "old": float(old), "new": new})

    if "partner_price" in want:
        prod = {p.id: p.name for p in (await db.execute(select(Product))).scalars().all()}
        for pp in (await db.execute(select(PartnerPrice).where(PartnerPrice.partner_id.in_(pids)))).scalars().all():
            add(pp.partner_id, "partner_price", str(pp.product_id), prod.get(pp.product_id, "termék"), pp.price_per_portion)
    if want & {"below_min", "below_min_kg", "tier", "rent_fee"}:
        contracts = (
            await db.execute(
                select(PartnerContract).where(PartnerContract.partner_id.in_(pids)).order_by(PartnerContract.valid_from)
            )
        ).scalars().all()
        for c in contracts:
            if c.valid_to is not None and c.valid_to < effective_from:
                continue  # a hatály előtt lejárt szerződést nem bántjuk
            tag = f"szerződés {c.valid_from.isoformat()}"
            if "below_min" in want:
                add(c.partner_id, "below_min", str(c.id), tag, c.below_min_price)
            if "below_min_kg" in want:
                add(c.partner_id, "below_min_kg", str(c.id), tag, c.below_min_price_kg)
            if "rent_fee" in want and c.fixed_rental:
                add(c.partner_id, "rent_fee", str(c.id), tag, c.rent_fee)
            if "tier" in want and c.price_tiers:
                for i, t in enumerate(c.price_tiers):
                    if not isinstance(t, dict):
                        continue
                    lo, hi = t.get("qty_from"), t.get("qty_to")
                    add(c.partner_id, "tier", f"{c.id}:{i}", f"{tag} · sáv {_num(lo or 0)}–{_num(hi) if hi is not None else '∞'}", t.get("price"))
    if "counter_price" in want:
        assets = (await db.execute(select(Asset).where(Asset.partner_id.in_(pids)))).scalars().all()
        for a in assets:
            if not a.counter_prices:
                continue
            for i, price in enumerate(a.counter_prices):
                if price is None:
                    continue
                cname = (a.counter_names or [None] * (i + 1))[i] if a.counter_names and i < len(a.counter_names) else None
                add(a.partner_id, "counter_price", f"{a.id}:{i}", f"{a.barcode} · {cname or f'számláló {i + 1}'}", price)
    rows.sort(key=lambda r: (r["partner"], r["kind"], r["label"]))
    return rows


async def _set_value(db: AsyncSession, row: dict, *, from_val: float, to_val: float) -> bool:
    """Egy sor átírása, csak ha az ár még a várt (from_val) értéken áll."""
    kind, ref = row["kind"], row["ref"]
    if kind == "partner_price":
        pp = (await db.execute(select(PartnerPrice).where(
            PartnerPrice.partner_id == _uuid(row["partner_id"]), PartnerPrice.product_id == _uuid(ref)))).scalar_one_or_none()
        if pp is None or abs(float(pp.price_per_portion) - from_val) > 1e-6:
            return False
        pp.price_per_portion = to_val
        return True
    if kind in ("below_min", "below_min_kg", "rent_fee"):
        c = await db.get(PartnerContract, _uuid(ref))
        attr = {"below_min": "below_min_price", "below_min_kg": "below_min_price_kg", "rent_fee": "rent_fee"}[kind]
        if c is None or getattr(c, attr) is None or abs(float(getattr(c, attr)) - from_val) > 1e-6:
            return False
        setattr(c, attr, to_val)
        return True
    if kind == "tier":
        cid, _, idx = ref.partition(":")
        c = await db.get(PartnerContract, _uuid(cid))
        i = int(idx)
        if c is None or not c.price_tiers or i >= len(c.price_tiers):
            return False
        tiers = [dict(t) if isinstance(t, dict) else t for t in c.price_tiers]
        if not isinstance(tiers[i], dict) or abs(float(tiers[i].get("price") or 0) - from_val) > 1e-6:
            return False
        tiers[i]["price"] = to_val
        c.price_tiers = tiers
        return True
    if kind == "counter_price":
        aid, _, idx = ref.partition(":")
        a = await db.get(Asset, _uuid(aid))
        i = int(idx)
        if a is None or not a.counter_prices or i >= len(a.counter_prices) or a.counter_prices[i] is None:
            return False
        if abs(float(a.counter_prices[i]) - from_val) > 1e-6:
            return False
        prices = list(a.counter_prices)
        prices[i] = to_val
        a.counter_prices = prices
        return True
    return False


async def _resync_partners(db: AsyncSession, partner_ids: set[str]) -> None:
    from app.services.wfm.contracts import apply_active_contract

    for pid in partner_ids:
        p = await db.get(Partner, _uuid(pid))
        if p is not None:
            await apply_active_contract(db, p)


def _ctx(ix: PriceIndexation, applied: int, skipped: int) -> dict:
    partners = {r["partner_id"] for r in (ix.rows or [])}
    return {
        "_indexation_id": str(ix.id), "nev": ix.name, "partnerek": len(partners), "tetelek": applied,
        "kihagyva": skipped, "szazalek": ix.percent or 0, "osszeg": ix.amount or 0,
        "hataly": ix.effective_from.isoformat(),
    }


async def apply_indexation(db: AsyncSession, ix: PriceIndexation, *, actor_id=None, now: datetime | None = None) -> dict:
    """Alkalmazás: a sorok átírása (ami közben kézzel megváltozott, kimarad),
    tükör-mezők frissítése, napló + esemény. Commitol."""
    from app.services.wfm.automation import fire_event
    from app.services.wfm.telegram import notify_event

    now = now or datetime.now(UTC)
    if ix.status != "scheduled":
        return {"applied": 0, "skipped": 0, "status": ix.status}
    applied, skipped = 0, 0
    # új dict-ek: a JSON-oszlop csak akkor látja a változást, ha az érték tényleg más objektum
    rows = [dict(r) for r in (ix.rows or [])]
    for r in rows:
        ok = await _set_value(db, r, from_val=float(r["old"]), to_val=float(r["new"]))
        r["applied"] = ok
        applied += int(ok)
        skipped += int(not ok)
    ix.rows = rows
    await _resync_partners(db, {r["partner_id"] for r in rows})
    ix.status = "applied"
    ix.applied_at = now
    db.add(AuditEvent(
        actor_user_id=actor_id, action="price.indexation_applied", entity_type="price_indexation",
        entity_id=str(ix.id),
        detail={"name": ix.name, "applied": applied, "skipped": skipped, "percent": ix.percent,
                "amount": ix.amount, "effective_from": ix.effective_from.isoformat()},
    ))
    await db.commit()
    ctx = _ctx(ix, applied, skipped)
    try:
        fire_event("price.indexation_applied", ctx)
    except Exception:  # noqa: BLE001
        logger.warning("indexation event failed", exc_info=True)
    try:
        await notify_event(db, "price.indexation_applied", ctx)
    except Exception:  # noqa: BLE001
        logger.warning("indexation telegram failed", exc_info=True)
    return {"applied": applied, "skipped": skipped, "status": ix.status}


async def revert_indexation(db: AsyncSession, ix: PriceIndexation, *, actor_id=None, now: datetime | None = None) -> dict:
    """Visszavonás: a régi árak vissza, csak ahol még az új érték áll. Commitol."""
    now = now or datetime.now(UTC)
    if ix.status != "applied":
        return {"reverted": 0, "skipped": 0, "status": ix.status}
    reverted, skipped = 0, 0
    rows = list(ix.rows or [])
    for r in rows:
        if not r.get("applied"):
            continue
        ok = await _set_value(db, r, from_val=float(r["new"]), to_val=float(r["old"]))
        reverted += int(ok)
        skipped += int(not ok)
    await _resync_partners(db, {r["partner_id"] for r in rows})
    ix.status = "reverted"
    ix.reverted_at = now
    db.add(AuditEvent(
        actor_user_id=actor_id, action="price.indexation_reverted", entity_type="price_indexation",
        entity_id=str(ix.id), detail={"name": ix.name, "reverted": reverted, "skipped": skipped},
    ))
    await db.commit()
    return {"reverted": reverted, "skipped": skipped, "status": ix.status}


async def run_scheduled_indexations(db: AsyncSession, today: date | None = None) -> int:
    """Óránkénti kör: a hatályba lépett, ütemezett áremelések alkalmazása."""
    today = today or date.today()
    due = (
        await db.execute(
            select(PriceIndexation).where(PriceIndexation.status == "scheduled", PriceIndexation.effective_from <= today)
            .order_by(PriceIndexation.effective_from)
        )
    ).scalars().all()
    n = 0
    for ix in due:
        try:
            await apply_indexation(db, ix)
            n += 1
        except Exception:  # noqa: BLE001
            logger.warning("scheduled indexation failed: %s", ix.id, exc_info=True)
            await db.rollback()
    return n


def ft(v: float) -> str:
    """Ft-összeg magyarosan: ezres szóközzel, tizedes csak ha van (39,9 Ft)."""
    v = float(v)
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}".replace(",", " ") + " Ft"
    whole, frac = f"{v:,.2f}".split(".")
    return whole.replace(",", " ") + "," + frac.rstrip("0") + " Ft"


def _num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def partner_table(rows: list[dict], partner_id: str) -> str:
    """Egy partner tételei szöveges táblában a levélhez."""
    lines = []
    for r in rows:
        if r["partner_id"] != partner_id:
            continue
        lines.append(f"- {TARGET_LABELS.get(r['kind'], r['kind'])}, {r['label']}: {ft(r['old'])} → {ft(r['new'])}")
    return "\n".join(lines)


def render_notice(template: str, *, partner_name: str, ix: PriceIndexation, company: str, table: str) -> str:
    pct = f"{ix.percent:g}%" if ix.percent else ""
    amt = ft(ix.amount) if ix.amount else ""
    mertek = " + ".join(x for x in (pct, amt) if x) or "—"
    out = template
    for k, v in {
        "partner_nev": partner_name, "hataly": ix.effective_from.strftime("%Y. %m. %d."), "mertek": mertek,
        "szazalek": f"{ix.percent:g}" if ix.percent else "0", "tabla": table, "ceg": company, "nev": ix.name,
    }.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out
