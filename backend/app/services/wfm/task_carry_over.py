"""El nem végzett feladatok automatikus átütemezése (153. kör, x-presso 973cd4bb).

Ami a határidejéig nem lett elvégezve (open / needs_more_work), az a napi
futásban a KÖVETKEZŐ MUNKANAPRA kerül — szerelőnél és képviselőnél egyformán,
beavatkozás nélkül. A feladaton marad a nyoma: original_due_date (az első
határidő), carry_over_count (hányszor csúszott), reschedule_note (naplósor),
és a vezérlőpult személyenként mutatja, kinek mennyi csúszott. A dolgozó
Telegramon értesül a mai napra átkerült feladatairól."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, Task
from app.services.wfm.holidays import is_working_day, next_working_day

logger = logging.getLogger(__name__)
OPEN_STATUSES = ("open", "needs_more_work")


def target_day(today: date) -> date:
    """A következő munkanap a mai naphoz képest: ha a mai nap munkanap, a mai."""
    return today if is_working_day(today) else next_working_day(today)


async def run_task_carry_over(db: AsyncSession, today: date | None = None, *, notify: bool = True) -> dict:
    """Minden lejárt határidejű, nyitott feladat → következő munkanap.
    Vissza: {"moved": n, "notified": k, "target": "ÉÉÉÉ-HH-NN"}."""
    today = today or date.today()
    target = target_day(today)
    rows = (
        await db.execute(
            select(Task, Employee)
            .join(Employee, Employee.id == Task.employee_id)
            .where(Task.status.in_(OPEN_STATUSES), Task.due_date < target)
            .order_by(Task.employee_id, Task.due_date)
        )
    ).all()
    per_employee: dict = {}
    for t, emp in rows:
        was = t.due_date
        if t.original_due_date is None:
            t.original_due_date = was
        t.carry_over_count = (t.carry_over_count or 0) + 1
        t.due_date = target
        line = f"{was:%Y-%m-%d} → {target:%Y-%m-%d}: automatikus átütemezés (nem lett elvégezve)"
        t.reschedule_note = (f"{t.reschedule_note}\n{line}" if t.reschedule_note else line)[-512:]
        per_employee.setdefault(emp.id, (emp, []))[1].append(t)
    moved = len(rows)
    if moved:
        await db.commit()
    notified = 0
    if notify and moved:
        from app.services.wfm.telegram import send_personal

        for emp, tasks in per_employee.values():
            titles = "\n".join(f"• {t.title}" for t in tasks[:10])
            more = f"\n…és még {len(tasks) - 10}" if len(tasks) > 10 else ""
            text = (
                f"↻ {len(tasks)} el nem végzett feladatod átkerült {target:%Y.%m.%d}-ra:\n{titles}{more}\n"
                "Ha valamelyik már nem aktuális, jelezd a vezetődnek."
            )
            try:
                if await send_personal(db, emp, text):
                    notified += 1
            except Exception:  # noqa: BLE001 — az értesítés hibája nem állítja meg a futást
                logger.warning("carry-over notify failed", exc_info=True)
    return {"moved": moved, "notified": notified, "target": target.isoformat()}
