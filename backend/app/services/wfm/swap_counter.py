"""Gépcsere leolvashatatlan számlálóval (168. kör, x-presso 9aee0a88).

Ha a kicserélt gép zárlatos, és a cserekor nem lehet leolvasni a záró
állást, a csere az ELŐZŐ (utoljára elszámolt) állással megy végbe, de a
függő gépcsere-elszámolás `counter_pending` jelzést kap: az elszámolás addig
nem zárja le, amíg a szervizben a tényleges záró állást nem rögzítik (a
Feladataim munkalapján vagy a Gépek oldalon). Az elszámolás ezután a
rögzített állással számol.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException

from app.models import Asset


def pending_swap(asset: Asset) -> dict | None:
    sp = asset.swap_pending if isinstance(asset.swap_pending, dict) else None
    return sp if sp and sp.get("counter_pending") else None


def apply_swap_counter(asset: Asset, counters: list[int] | None, counter: int | None) -> int:
    """A függő gépcsere záró állásának utólagos rögzítése. Visszaadja a záró
    összesített állást. 422, ha nincs függő rögzítés vagy az állás kisebb az
    előzőnél."""
    sp = pending_swap(asset)
    if sp is None:
        raise HTTPException(status_code=422, detail={"code": "asset.no_pending_swap_counter"})
    final = sum(int(x) for x in counters) if counters else counter
    if final is None:
        raise HTTPException(status_code=422, detail={"code": "asset.counter_required"})
    prev = int(sp.get("counter") or 0)
    if final < prev:
        raise HTTPException(status_code=422, detail={"code": "settlement.counter_below_prev", "barcode": asset.barcode})
    new_sp = dict(sp)
    new_sp["counters"] = [int(x) for x in counters] if counters else None
    new_sp["counter"] = int(final)
    new_sp.pop("counter_pending", None)
    new_sp["counter_recorded_at"] = datetime.now(UTC).isoformat()
    asset.swap_pending = new_sp  # új dict → a JSON oszlop biztosan módosultnak számít
    return int(final)
