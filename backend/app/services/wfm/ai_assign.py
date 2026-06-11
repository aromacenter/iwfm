"""AI-alapú feladat-kiosztási javaslat.

A folyamat:
1. Determinisztikus kandidátus-gyűjtés (csak aktív dolgozók; jelöljük a
   skill-egyezést, az aznapi jóváhagyott távollétet és a nyitott feladatok
   számát — az AI ezekből dolgozik, nem találgat).
2. Prompt az aktív szolgáltatónak (Claude / Gemini), szigorú JSON-választ kérve.
3. Robusztus parse + validáció: csak létező dolgozó-ID fogadható el.

A prompt-építés és a parse tiszta függvény — unit-tesztelhető AI-hívás nélkül.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Employee, EmployeeSkill, Skill, Task, TimeOffRequest
from app.services.wfm.ai_service import generate


@dataclass
class Candidate:
    id: str
    name: str
    job_title: str | None
    skills: list[str] = field(default_factory=list)
    has_required_skill: bool = False
    on_leave: bool = False
    open_tasks: int = 0


async def collect_candidates(
    db: AsyncSession, *, due_date: date, required_skill_id: int | None
) -> list[Candidate]:
    employees = (
        (
            await db.execute(
                select(Employee).where(Employee.status == "active").order_by(Employee.last_name)
            )
        )
        .scalars()
        .all()
    )
    if not employees:
        return []
    emp_ids = [e.id for e in employees]

    skill_rows = (
        await db.execute(
            select(EmployeeSkill.employee_id, EmployeeSkill.skill_id, Skill.name)
            .join(Skill, Skill.id == EmployeeSkill.skill_id)
            .where(EmployeeSkill.employee_id.in_(emp_ids))
        )
    ).all()
    skills_by_emp: dict[uuid.UUID, list[tuple[int, str]]] = {}
    for emp_id, skill_id, name in skill_rows:
        skills_by_emp.setdefault(emp_id, []).append((skill_id, name))

    on_leave_ids = set(
        (
            await db.execute(
                select(TimeOffRequest.employee_id).where(
                    TimeOffRequest.status == "approved",
                    TimeOffRequest.start_date <= due_date,
                    TimeOffRequest.end_date >= due_date,
                )
            )
        ).scalars()
    )

    task_counts = dict(
        (
            await db.execute(
                select(Task.employee_id, func.count())
                .where(Task.status != "done", Task.employee_id.in_(emp_ids))
                .group_by(Task.employee_id)
            )
        ).all()
    )

    out = []
    for emp in employees:
        emp_skills = skills_by_emp.get(emp.id, [])
        out.append(
            Candidate(
                id=str(emp.id),
                name=f"{emp.last_name} {emp.first_name}",
                job_title=emp.job_title,
                skills=[name for _, name in emp_skills],
                has_required_skill=(
                    required_skill_id is None
                    or any(sid == required_skill_id for sid, _ in emp_skills)
                ),
                on_leave=emp.id in on_leave_ids,
                open_tasks=int(task_counts.get(emp.id, 0)),
            )
        )
    return out


def build_prompt(
    candidates: list[Candidate],
    *,
    title: str,
    description: str | None,
    due_date: date,
    required_skill_name: str | None,
) -> str:
    lines = []
    for c in candidates:
        flags = []
        if required_skill_name:
            flags.append(
                "RENDELKEZIK a szükséges skillel"
                if c.has_required_skill
                else "NEM rendelkezik a szükséges skillel"
            )
        if c.on_leave:
            flags.append("AZNAP SZABADSÁGON VAN")
        flags.append(f"nyitott feladatai: {c.open_tasks}")
        skills = ", ".join(c.skills) if c.skills else "nincs rögzített skill"
        lines.append(
            f"- id: {c.id} | {c.name}"
            + (f" ({c.job_title})" if c.job_title else "")
            + f" | skillek: {skills} | {' | '.join(flags)}"
        )

    return (
        "Munkahelyi feladat-kiosztásban segítesz. Válaszd ki az alábbi dolgozók "
        "közül a LEGALKALMASABBAT a feladatra.\n\n"
        f"Feladat: {title}\n"
        + (f"Leírás: {description}\n" if description else "")
        + f"Határidő: {due_date.isoformat()}\n"
        + (f"Szükséges skill: {required_skill_name}\n" if required_skill_name else "")
        + "\nDolgozók:\n"
        + "\n".join(lines)
        + "\n\nSzempontok fontossági sorrendben: 1) aki aznap szabadságon van, "
        "azt NE válaszd; 2) a szükséges skillel rendelkezőt részesítsd előnyben; "
        "3) az alacsonyabb terhelésűt (kevesebb nyitott feladat) részesítsd "
        "előnyben; 4) a munkakör illeszkedése.\n\n"
        'KIZÁRÓLAG ezzel a JSON-nal válaszolj, más szöveg nélkül: '
        '{"employee_id": "<a kiválasztott dolgozó id-ja>", '
        '"reason": "<1-2 mondatos magyar indoklás>"}'
    )


def parse_suggestion(text: str, valid_ids: set[str]) -> dict | None:
    """A modell válaszából {employee_id, reason} — None, ha értelmezhetetlen."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip().strip("`")
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    emp_id = str(data.get("employee_id", "")).strip()
    if emp_id not in valid_ids:
        return None
    return {"employee_id": emp_id, "reason": str(data.get("reason", "")).strip()[:500]}


async def suggest_assignee(
    db: AsyncSession,
    *,
    title: str,
    description: str | None,
    due_date: date,
    required_skill_id: int | None,
) -> dict:
    """AI-javaslat. ValueError kódokkal: ai_not_configured / no_candidates /
    unparseable — a hívó réteg fordítja HTTP-hibára."""
    candidates = await collect_candidates(
        db, due_date=due_date, required_skill_id=required_skill_id
    )
    if not candidates:
        raise ValueError("no_candidates")

    required_skill_name = None
    if required_skill_id is not None:
        required_skill_name = (
            await db.execute(select(Skill.name).where(Skill.id == required_skill_id))
        ).scalar_one_or_none()

    prompt = build_prompt(
        candidates,
        title=title,
        description=description,
        due_date=due_date,
        required_skill_name=required_skill_name,
    )
    reply = await generate(db, prompt, max_tokens=400)  # ValueError: ai_not_configured

    parsed = parse_suggestion(reply, {c.id for c in candidates})
    if parsed is None:
        raise ValueError("unparseable")

    chosen = next(c for c in candidates if c.id == parsed["employee_id"])
    return {
        "employee_id": parsed["employee_id"],
        "employee_name": chosen.name,
        "reason": parsed["reason"],
        "on_leave": chosen.on_leave,
        "has_required_skill": chosen.has_required_skill,
        "open_tasks": chosen.open_tasks,
    }
