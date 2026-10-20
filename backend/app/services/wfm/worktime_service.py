"""Munkaidő-mérés (worktime modul, 1d553d2e C fázis) — a timer tiszta
logikája: munkamenetek (sessions) listája, mindegyikben szünetek.

sessions = [{"start": iso, "end": iso|None, "pauses": [{"p": iso, "r": iso|None}, …]}, …]

A munkalap megnyitásakor új munkamenet indul (ha nincs nyitott), a „Szünet”
pauzál, a mentés/bezárás zár. A szerelő a számokat NEM látja (csak azt, hogy
fut), a vezetői statisztika a mért aktív időt veti össze a munkalapra írt
órával."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class TimerSegment(BaseModel):
    kind: str  # work | pause
    start: datetime
    end: datetime | None


class TimerSummary(BaseModel):
    """Vezetői kép: számokkal."""

    state: str  # running | paused | stopped | none
    first_started_at: datetime | None
    last_ended_at: datetime | None
    active_seconds: float
    pause_seconds: float
    pause_count: int
    session_count: int
    segments: list[TimerSegment] = []


def state_of(sessions: list[dict]) -> str:
    if not sessions:
        return "none"
    last = sessions[-1]
    if last.get("end"):
        return "stopped"
    pauses = last.get("pauses") or []
    if pauses and not pauses[-1].get("r"):
        return "paused"
    return "running"


def start(sessions: list[dict], now: datetime) -> list[dict]:
    """Új munkamenet, ha nincs nyitott (idempotens)."""
    if sessions and not sessions[-1].get("end"):
        return sessions
    return sessions + [{"start": _iso(now), "end": None, "pauses": []}]


def pause(sessions: list[dict], now: datetime) -> list[dict]:
    if state_of(sessions) != "running":
        return sessions
    out = [dict(s) for s in sessions]
    last = out[-1]
    last["pauses"] = list(last.get("pauses") or []) + [{"p": _iso(now), "r": None}]
    return out


def resume(sessions: list[dict], now: datetime) -> list[dict]:
    if state_of(sessions) != "paused":
        return sessions
    out = [dict(s) for s in sessions]
    last = out[-1]
    pauses = [dict(p) for p in (last.get("pauses") or [])]
    pauses[-1]["r"] = _iso(now)
    last["pauses"] = pauses
    return out


def stop(sessions: list[dict], now: datetime) -> list[dict]:
    st = state_of(sessions)
    if st in ("none", "stopped"):
        return sessions
    out = resume(sessions, now) if st == "paused" else [dict(s) for s in sessions]
    out[-1]["end"] = _iso(now)
    return out


def summarize(sessions: list[dict], now: datetime | None = None) -> TimerSummary:
    """Aktív idő = munkamenet hossza − szünetek; nyitott munkamenet/szünet
    `now`-ig számít."""
    now = now or datetime.now(UTC)
    active = 0.0
    paused = 0.0
    pause_count = 0
    segments: list[TimerSegment] = []
    first: datetime | None = None
    last_end: datetime | None = None
    for s in sessions:
        start_dt = _parse(s.get("start"))
        if start_dt is None:
            continue
        end_dt = _parse(s.get("end"))
        first = start_dt if first is None or start_dt < first else first
        if end_dt is not None:
            last_end = end_dt if last_end is None or end_dt > last_end else last_end
        eff_end = end_dt or now
        cursor = start_dt
        for p in s.get("pauses") or []:
            p_dt, r_dt = _parse(p.get("p")), _parse(p.get("r"))
            if p_dt is None:
                continue
            pause_count += 1
            if p_dt > cursor:
                segments.append(TimerSegment(kind="work", start=cursor, end=p_dt))
                active += (p_dt - cursor).total_seconds()
            r_eff = r_dt or eff_end
            segments.append(TimerSegment(kind="pause", start=p_dt, end=r_dt))
            paused += max(0.0, (r_eff - p_dt).total_seconds())
            cursor = r_eff
        if eff_end > cursor:
            segments.append(TimerSegment(kind="work", start=cursor, end=end_dt))
            active += (eff_end - cursor).total_seconds()
    return TimerSummary(
        state=state_of(sessions), first_started_at=first, last_ended_at=last_end,
        active_seconds=round(active, 1), pause_seconds=round(paused, 1), pause_count=pause_count,
        session_count=len(sessions), segments=segments,
    )
