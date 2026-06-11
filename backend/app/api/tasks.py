"""Feladatok (kiosztás + dolgozói visszajelzés).

Manager oldal: CRUD + kiosztás (opcionális skill-követelménnyel — a későbbi
AI-alapú kiosztás előkészítése). Dolgozói oldal (me): saját feladatok,
komment írása, státusz: 'done' (befejezett) / 'needs_more_work'
(további munkát igényel).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_own_employee,
    record_audit,
    require_role,
)
from app.db import get_db
from app.models import Employee, Skill, Task, TaskComment, User

router = APIRouter()
me_router = APIRouter()  # /api/me/tasks alá kerül

EMPLOYEE_STATUSES = ("done", "needs_more_work")
ALL_STATUSES = ("open", "done", "needs_more_work")


# ─── Schemas ─────────────────────────────────────────────────────────────────


class TaskCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4000)
    employee_id: str
    due_date: date
    required_skill_id: int | None = None


class TaskPatchBody(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    employee_id: str | None = None
    due_date: date | None = None
    required_skill_id: int | None = None
    status: str | None = None  # manager bármit állíthat (pl. visszanyitás)


class CommentBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class StatusBody(BaseModel):
    status: str  # done | needs_more_work
    comment: str | None = Field(default=None, max_length=1000)


class CommentOut(BaseModel):
    id: str
    author_name: str | None
    text: str
    created_at: datetime


class TaskOut(BaseModel):
    id: str
    title: str
    description: str | None
    employee_id: str
    employee_name: str | None
    due_date: date
    required_skill: dict | None
    status: str
    comments: list[CommentOut]
    created_at: datetime


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _comments_map(
    db: AsyncSession, task_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[CommentOut]]:
    if not task_ids:
        return {}
    rows = (
        await db.execute(
            select(TaskComment, User.display_name)
            .outerjoin(User, User.id == TaskComment.author_user_id)
            .where(TaskComment.task_id.in_(task_ids))
            .order_by(TaskComment.created_at)
        )
    ).all()
    out: dict[uuid.UUID, list[CommentOut]] = {}
    for c, author in rows:
        out.setdefault(c.task_id, []).append(
            CommentOut(id=str(c.id), author_name=author, text=c.text, created_at=c.created_at)
        )
    return out


async def _tasks_out(db: AsyncSession, tasks: list[Task]) -> list[TaskOut]:
    emp_rows = (
        await db.execute(
            select(Employee.id, Employee.last_name, Employee.first_name).where(
                Employee.id.in_({t.employee_id for t in tasks})
            )
        )
    ).all()
    names = {eid: f"{ln} {fn}" for eid, ln, fn in emp_rows}
    skill_rows = (
        await db.execute(
            select(Skill.id, Skill.name).where(
                Skill.id.in_({t.required_skill_id for t in tasks if t.required_skill_id})
            )
        )
    ).all()
    skills = {sid: name for sid, name in skill_rows}
    comments = await _comments_map(db, [t.id for t in tasks])
    return [
        TaskOut(
            id=str(t.id),
            title=t.title,
            description=t.description,
            employee_id=str(t.employee_id),
            employee_name=names.get(t.employee_id),
            due_date=t.due_date,
            required_skill=(
                {"id": t.required_skill_id, "name": skills[t.required_skill_id]}
                if t.required_skill_id and t.required_skill_id in skills
                else None
            ),
            status=t.status,
            comments=comments.get(t.id, []),
            created_at=t.created_at,
        )
        for t in tasks
    ]


async def _get_task_or_404(db: AsyncSession, task_id: str) -> Task:
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "tasks.not_found"})
    task = (await db.execute(select(Task).where(Task.id == tid))).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "tasks.not_found"})
    return task


# ─── Manager endpoints ───────────────────────────────────────────────────────


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    query = select(Task).order_by(Task.due_date.desc(), Task.created_at.desc())
    if date_from:
        query = query.where(Task.due_date >= date_from)
    if date_to:
        query = query.where(Task.due_date <= date_to)
    if status:
        if status not in ALL_STATUSES:
            raise HTTPException(status_code=422, detail={"code": "tasks.bad_status"})
        query = query.where(Task.status == status)
    tasks = list((await db.execute(query.limit(500))).scalars())
    return await _tasks_out(db, tasks)


@router.post("", response_model=TaskOut, status_code=201)
async def create_task(
    body: TaskCreateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    try:
        emp_id = uuid.UUID(body.employee_id)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "tasks.bad_employee"})
    emp = (
        await db.execute(
            select(Employee).where(Employee.id == emp_id, Employee.status == "active")
        )
    ).scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=422, detail={"code": "tasks.bad_employee"})

    task = Task(
        title=body.title.strip(),
        description=body.description,
        employee_id=emp_id,
        due_date=body.due_date,
        required_skill_id=body.required_skill_id,
        created_by=actor.id,
    )
    db.add(task)
    await db.flush()
    await record_audit(
        db, actor=actor, action="task.create", entity_type="task",
        entity_id=str(task.id), request=request,
    )
    await db.commit()
    return (await _tasks_out(db, [task]))[0]


@router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: str,
    body: TaskPatchBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    task = await _get_task_or_404(db, task_id)
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in ALL_STATUSES:
        raise HTTPException(status_code=422, detail={"code": "tasks.bad_status"})
    if "employee_id" in data:
        try:
            data["employee_id"] = uuid.UUID(data["employee_id"])
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "tasks.bad_employee"})
    for key, value in data.items():
        setattr(task, key, value)
    await record_audit(
        db, actor=actor, action="task.update", entity_type="task",
        entity_id=str(task.id), detail={"fields": sorted(data)}, request=request,
    )
    await db.commit()
    return (await _tasks_out(db, [task]))[0]


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    task = await _get_task_or_404(db, task_id)
    await db.delete(task)
    await record_audit(
        db, actor=actor, action="task.delete", entity_type="task",
        entity_id=str(task.id), request=request,
    )
    await db.commit()
    return {"ok": True}


@router.post("/{task_id}/comment", response_model=TaskOut)
async def manager_comment(
    task_id: str,
    body: CommentBody,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("manager")),
):
    task = await _get_task_or_404(db, task_id)
    db.add(TaskComment(task_id=task.id, author_user_id=actor.id, text=body.text.strip()))
    await db.commit()
    return (await _tasks_out(db, [task]))[0]


# ─── Employee (self-service) endpoints — /api/me/tasks ──────────────────────


@me_router.get("", response_model=list[TaskOut])
async def my_tasks(
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
):
    tasks = list(
        (
            await db.execute(
                select(Task)
                .where(Task.employee_id == emp.id)
                .order_by(Task.status == "done", Task.due_date.desc())
                .limit(100)
            )
        ).scalars()
    )
    return await _tasks_out(db, tasks)


async def _own_task_or_404(db: AsyncSession, emp: Employee, task_id: str) -> Task:
    task = await _get_task_or_404(db, task_id)
    if task.employee_id != emp.id:  # idegen feladat: nem látható, 404
        raise HTTPException(status_code=404, detail={"code": "tasks.not_found"})
    return task


@me_router.post("/{task_id}/comment", response_model=TaskOut)
async def my_comment(
    task_id: str,
    body: CommentBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    task = await _own_task_or_404(db, emp, task_id)
    db.add(TaskComment(task_id=task.id, author_user_id=user.id, text=body.text.strip()))
    await record_audit(
        db, actor=user, action="task.self_comment", entity_type="task",
        entity_id=str(task.id), request=request,
    )
    await db.commit()
    return (await _tasks_out(db, [task]))[0]


@me_router.post("/{task_id}/status", response_model=TaskOut)
async def my_status(
    task_id: str,
    body: StatusBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    emp: Employee = Depends(get_own_employee),
    user: User = Depends(get_current_user),
):
    if body.status not in EMPLOYEE_STATUSES:
        raise HTTPException(status_code=422, detail={"code": "tasks.bad_status"})
    task = await _own_task_or_404(db, emp, task_id)
    task.status = body.status
    if body.comment:
        db.add(TaskComment(task_id=task.id, author_user_id=user.id, text=body.comment.strip()))
    await record_audit(
        db, actor=user, action=f"task.self_{body.status}", entity_type="task",
        entity_id=str(task.id), request=request,
    )
    await db.commit()
    return (await _tasks_out(db, [task]))[0]
