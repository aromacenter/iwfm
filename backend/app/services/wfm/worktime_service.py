"""Munkaidő-mérés (worktime modul, 1d553d2e C fázis + 3. kör) — a timer
tiszta logikája: munkamenetek (sessions) listája, mindegyikben szünetek és
időtípus-váltások.

sessions = [{"start": iso, "end": iso|None, "mode": "work"|"travel"|"wait",
             "pauses": [{"p": iso, "r": iso|None}, …],
             "modes": [{"at": iso, "mode": "…"}, …]}, …]

A munkalap megnyitásakor új munkamenet indul (ha nincs nyitott), a „Szünet”
pauzál, az időtípus (🚗 út / 🔧 munka / ⏳ várakozás) a munkamenet alatt
váltható, a mentés/bezárás zár. A szerelő a számokat NEM látja (csak azt,
hogy fut), a vezetői statisztika a mért aktív időt veti össze a munkalapra
írt órával."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

MODES = ("work", "travel", "wait")


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class TimerSegment(BaseModel):
    kind: str  # work | travel | wait | pause
    start: datetime
    end: datetime | None


class TimerSummary(BaseModel):
    """Vezetői kép: számokkal."""

    state: str  # running | paused | stopped | none
    mode: str = "work"
    first_started_at: datetime | None
    last_ended_at: datetime | None
    active_seconds: float  # minden nem-szünet idő (work + travel + wait)
    work_seconds: float = 0.0
    travel_seconds: float = 0.0
    wait_seconds: float = 0.0
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


def current_mode(sessions: list[dict]) -> str:
    if not sessions:
        return "work"
    last = sessions[-1]
    modes = last.get("modes") or []
    return (modes[-1].get("mode") if modes else last.get("mode")) or "work"


def start(sessions: list[dict], now: datetime, mode: str = "work") -> list[dict]:
    """Új munkamenet, ha nincs nyitott (idempotens)."""
    if sessions and not sessions[-1].get("end"):
        return sessions
    return sessions + [{"start": _iso(now), "end": None, "mode": mode if mode in MODES else "work", "pauses": [], "modes": []}]


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


def set_mode(sessions: list[dict], now: datetime, mode: str) -> list[dict]:
    """Időtípus-váltás a nyitott munkamenetben (szünet alatt is beállítható —
    a folytatástól számít)."""
    if mode not in MODES or state_of(sessions) in ("none", "stopped") or current_mode(sessions) == mode:
        return sessions
    out = [dict(s) for s in sessions]
    last = out[-1]
    last["modes"] = list(last.get("modes") or []) + [{"at": _iso(now), "mode": mode}]
    return out


def stop(sessions: list[dict], now: datetime) -> list[dict]:
    st = state_of(sessions)
    if st in ("none", "stopped"):
        return sessions
    out = resume(sessions, now) if st == "paused" else [dict(s) for s in sessions]
    out[-1]["end"] = _iso(now)
    return out


def summarize(sessions: list[dict], now: datetime | None = None) -> TimerSummary:
    """Aktív idő = munkamenet hossza − szünetek, időtípusonként bontva; a
    nyitott munkamenet / szünet `now`-ig számít."""
    now = now or datetime.now(UTC)
    by_mode = {"work": 0.0, "travel": 0.0, "wait": 0.0}
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
        # események időrendben: szünet-kezdés/-vég és módváltás
        events: list[tuple[datetime, str, str]] = []
        for p in s.get("pauses") or []:
            p_dt, r_dt = _parse(p.get("p")), _parse(p.get("r"))
            if p_dt is None:
                continue
            events.append((p_dt, "pause", ""))
            events.append((r_dt or eff_end, "resume", ""))
        for m in s.get("modes") or []:
            m_dt = _parse(m.get("at"))
            if m_dt is not None and m.get("mode") in MODES:
                events.append((m_dt, "mode", m["mode"]))
        events.sort(key=lambda e: e[0])
        mode = s.get("mode") if s.get("mode") in MODES else "work"
        in_pause = False
        cursor = start_dt
        for at, kind, val in events:
            at = min(max(at, cursor), eff_end)
            if at > cursor:
                segments.append(TimerSegment(kind="pause" if in_pause else mode, start=cursor, end=at))
                if in_pause:
                    paused += (at - cursor).total_seconds()
                else:
                    by_mode[mode] += (at - cursor).total_seconds()
                cursor = at
            if kind == "pause" and not in_pause:
                in_pause = True
                pause_count += 1
            elif kind == "resume":
                in_pause = False
            elif kind == "mode":
                mode = val
        if eff_end > cursor:
            segments.append(TimerSegment(kind="pause" if in_pause else mode, start=cursor, end=end_dt))
            if in_pause:
                paused += (eff_end - cursor).total_seconds()
            else:
                by_mode[mode] += (eff_end - cursor).total_seconds()
    active = sum(by_mode.values())
    return TimerSummary(
        state=state_of(sessions), mode=current_mode(sessions), first_started_at=first, last_ended_at=last_end,
        active_seconds=round(active, 1), work_seconds=round(by_mode["work"], 1), travel_seconds=round(by_mode["travel"], 1),
        wait_seconds=round(by_mode["wait"], 1), pause_seconds=round(paused, 1), pause_count=pause_count,
        session_count=len(sessions), segments=segments,
    )
