"""Csapatos munkavégzés (136. kör): a feladat tagjai = fő felelős + segítők.
Közös segédek a me-végpontokhoz, a munkaidő-méréshez és a beépítéshez."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, Task, TaskHelper


def helper_task_ids(employee_id: uuid.UUID):
    """Al-lekérdezés: azok a feladatok, ahol a dolgozó segítő."""
    return select(TaskHelper.task_id).where(TaskHelper.employee_id == employee_id)


async def helper_ids(db: AsyncSession, task_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (await db.execute(select(TaskHelper.employee_id).where(TaskHelper.task_id == task_id))).scalars()
    )


async def is_member(db: AsyncSession, task: Task, employee_id: uuid.UUID) -> bool:
    if task.employee_id == employee_id:
        return True
    row = (
        await db.execute(
            select(TaskHelper.id).where(TaskHelper.task_id == task.id, TaskHelper.employee_id == employee_id)
        )
    ).first()
    return row is not None


async def parse_helper_ids(db: AsyncSession, raw: list[str] | None, owner_id: uuid.UUID | None) -> list[uuid.UUID] | None:
    """Segítő-azonosítók ellenőrzése: UUID + létező dolgozó; a fő felelős kimarad.
    None = nem nyúlunk hozzá."""
    if raw is None:
        return None
    ids: list[uuid.UUID] = []
    for x in raw:
        try:
            v = uuid.UUID(str(x))
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "tasks.bad_employee"})
        if v != owner_id and v not in ids:
            ids.append(v)
    if ids:
        found = set(
            (await db.execute(select(Employee.id).where(Employee.id.in_(ids)))).scalars()
        )
        if found != set(ids):
            raise HTTPException(status_code=422, detail={"code": "tasks.bad_employee"})
    return ids


async def set_helpers(db: AsyncSession, task: Task, ids: list[uuid.UUID]) -> None:
    """A segítő-lista cseréje (a fő felelős sosem segítő). NEM commitol."""
    current = {
        h.employee_id: h
        for h in (await db.execute(select(TaskHelper).where(TaskHelper.task_id == task.id))).scalars()
    }
    wanted = {i for i in ids if i != task.employee_id}
    for eid, h in current.items():
        if eid not in wanted:
            await db.delete(h)
    for eid in wanted:
        if eid not in current:
            db.add(TaskHelper(task_id=task.id, employee_id=eid))
    await db.flush()
