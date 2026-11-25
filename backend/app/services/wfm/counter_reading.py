"""173. kör: számláló-állás bekérése átvételkor / átadáskor (x-presso 8fb91fca).

Kötelező és nem lehet nulla. Több számlálós gépnél (Asset.counter_count > 1) a
számlálónkénti lista kell (counters), az összeg a counter; egy számlálósnál a
counter elég. Az elismervényekre a számlálónkénti állás kerül a nevekkel."""

from __future__ import annotations

from fastapi import HTTPException


def validate_reading(asset, counter: int | None, counters: list[int] | None, *, code: str) -> tuple[int, list[int] | None]:
    """(összeg, lista|None). HTTP 422 {code}, ha hiányzik vagy nulla."""
    count = int(getattr(asset, "counter_count", 1) or 1) if asset is not None else 1
    if counters:
        vals = [int(v) for v in counters]
        if len(vals) != count or any(v < 1 for v in vals):
            raise HTTPException(status_code=422, detail={"code": code, "counters": count})
        return sum(vals), vals
    if count > 1:
        raise HTTPException(status_code=422, detail={"code": code, "counters": count})
    if counter is None or int(counter) < 1:
        raise HTTPException(status_code=422, detail={"code": code})
    return int(counter), None


def reading_text(asset, counter: int | None, counters: list | None) -> str | None:
    """„12 345” vagy „Kávé: 10 000 · Tea: 2 345 (összesen 12 345)” — az elismervényekre."""
    if counter is None and not counters:
        return None
    if counters and len(counters) > 1:
        names = list(getattr(asset, "counter_names", None) or []) if asset is not None else []
        parts = []
        for i, v in enumerate(counters):
            name = names[i] if i < len(names) and names[i] else f"{i + 1}. számláló"
            parts.append(f"{name}: {int(v):,}".replace(",", " "))
        return " · ".join(parts) + f" (összesen {int(counter or sum(counters)):,})".replace(",", " ")
    return f"{int(counter if counter is not None else counters[0]):,}".replace(",", " ")
