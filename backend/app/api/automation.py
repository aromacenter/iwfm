"""E-mail sablonok + automatizálási szabályok (Flow-szerű) — admin."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_role
from app.db import get_db
from app.models import AutomationRule, EmailTemplate, User, WorksheetSettings
from app.services.wfm.automation import ACTION_TYPES, OPS, TRIGGERS

router = APIRouter(prefix="/api/automation", tags=["automation"])


# ─── E-mail sablonok ─────────────────────────────────────────────────────────


class TemplateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=256)
    body: str = Field(min_length=1, max_length=20_000)


class TemplateOut(BaseModel):
    id: str
    name: str
    subject: str
    body: str
    updated_at: datetime


def _template_out(t: EmailTemplate) -> TemplateOut:
    return TemplateOut(id=str(t.id), name=t.name, subject=t.subject, body=t.body,
                       updated_at=t.updated_at)


async def _get_template(db: AsyncSession, template_id: str) -> EmailTemplate:
    try:
        tid = uuid.UUID(template_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "template.not_found"})
    t = (
        await db.execute(select(EmailTemplate).where(EmailTemplate.id == tid))
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "template.not_found"})
    return t


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))
):
    rows = (await db.execute(select(EmailTemplate).order_by(EmailTemplate.name))).scalars().all()
    return [_template_out(t) for t in rows]


@router.post("/templates", response_model=TemplateOut, status_code=201)
async def create_template(
    body: TemplateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    t = EmailTemplate(**body.model_dump())
    db.add(t)
    await db.flush()
    await record_audit(db, actor=actor, action="template.create", entity_type="email_template",
                       entity_id=str(t.id), detail={"name": t.name}, request=request)
    await db.commit()
    await db.refresh(t)  # server-default created_at/updated_at betöltése
    return _template_out(t)


@router.patch("/templates/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: str,
    body: TemplateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    t = await _get_template(db, template_id)
    for key, value in body.model_dump().items():
        setattr(t, key, value)
    await record_audit(db, actor=actor, action="template.update", entity_type="email_template",
                       entity_id=str(t.id), detail={"name": t.name}, request=request)
    await db.commit()
    await db.refresh(t)  # a szerver-oldali updated_at (onupdate) frissül
    return _template_out(t)


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    t = await _get_template(db, template_id)
    await db.execute(sa_delete(EmailTemplate).where(EmailTemplate.id == t.id))
    await record_audit(db, actor=actor, action="template.delete", entity_type="email_template",
                       entity_id=template_id, detail={"name": t.name}, request=request)
    await db.commit()
    return {"ok": True}


# ─── AI sablon-generálás ─────────────────────────────────────────────────────


class TemplateAIBody(BaseModel):
    description: str = Field(min_length=3, max_length=2000)
    trigger: str | None = None  # opcionális: melyik eseményhez készül

    @field_validator("trigger")
    @classmethod
    def _check_trigger(cls, v: str | None) -> str | None:
        if v and v not in TRIGGERS:
            raise ValueError("automation.bad_trigger")
        return v or None


class TemplateAIOut(BaseModel):
    name: str
    subject: str
    body: str


def _tpl_ai_prompt(description: str, trigger: str | None, company: str | None) -> str:
    if trigger:
        vars_txt = (
            f"A sablon a(z) \u201e{trigger}\u201d automatizálási eseményhez készül. "
            "KIZÁRÓLAG ezek a {{változók}} használhatók benne: "
            + ", ".join("{{" + v + "}}" for v in TRIGGERS[trigger])
        )
    else:
        lines = "\n".join(
            f"- {ev}: " + ", ".join("{{" + v + "}}" for v in fields)
            for ev, fields in TRIGGERS.items()
        )
        vars_txt = (
            "Az automatizálási eseményeknél elérhető {{változók}}:\n" + lines +
            "\nCsak olyan változót használj, amely a leírás szerinti eseménynél elérhető."
        )
    sender = f"a(z) {company} cég" if company else "egy kávégép-bérbeadó és bizományos cég"
    return (
        f"Írj magyar nyelvű e-mail sablont, amelyet {sender} küld a partnereinek "
        "(kávégép-üzemeltetés, kávé-bizomány, elszámolások, szerviz témakörben).\n\n"
        f"A sablon célja, az adminisztrátor leírása szerint:\n{description}\n\n"
        f"{vars_txt}\n\n"
        "Add vissza KIZÁRÓLAG ezt a JSON objektumot, minden más szöveg nélkül:\n"
        '{"name": "rövid sablonnév (max 6 szó)", "subject": "e-mail tárgy", '
        '"body": "az e-mail teljes szövege"}\n\n'
        "Szabályok:\n"
        "- Hangnem: udvarias, professzionális, magázó.\n"
        "- A body sima szöveg legyen (nem HTML), bekezdésekkel, megszólítással és "
        "elköszönéssel.\n"
        "- A dinamikus adatokat {{változó}} formában illeszd be a tárgyba és a "
        "szövegbe, ahol értelmes.\n"
        "- Ne találj ki olyan adatot (ár, dátum, telefonszám), amely nem változó."
    )


def _parse_tpl_json(text: str) -> dict | None:
    """A modellválasz JSON objektumának kinyerése (kódblokk-kerítés tűrve)."""
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("name") or "").strip()
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    if not subject or not body:
        return None
    return {
        "name": (name or subject)[:128],
        "subject": subject[:256],
        "body": body[:20_000],
    }


@router.post("/templates/generate", response_model=TemplateAIOut)
async def generate_template(
    body: TemplateAIBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """AI-vázlat egy e-mail sablonhoz — nem ment, a kliens űrlapját tölti ki.
    422 settings.ai_not_configured, ha nincs AI-kulcs; 502 automation.ai_failed."""
    from app.services.wfm import ai_service

    ws = (
        await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))
    ).scalar_one_or_none()
    company = (ws.company_name or "").strip() if ws else None
    prompt = _tpl_ai_prompt(body.description.strip(), body.trigger, company or None)
    try:
        text = await ai_service.generate(db, prompt, max_tokens=1500)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "settings.ai_not_configured"})
    except Exception:
        raise HTTPException(status_code=502, detail={"code": "automation.ai_failed"})
    parsed = _parse_tpl_json(text)
    if parsed is None:
        raise HTTPException(status_code=502, detail={"code": "automation.ai_failed"})
    return TemplateAIOut(**parsed)


# ─── Automatizálási szabályok ────────────────────────────────────────────────


class ConditionIn(BaseModel):
    field: str = Field(min_length=1, max_length=64)
    op: str = Field(default="eq")
    value: str = Field(default="", max_length=256)

    @field_validator("op")
    @classmethod
    def _check_op(cls, v: str) -> str:
        if v not in OPS:
            raise ValueError("automation.bad_op")
        return v


class ActionIn(BaseModel):
    type: str
    template_id: str | None = None
    to: str | None = Field(default=None, max_length=512)
    text: str | None = Field(default=None, max_length=2000)
    title: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    employee: str | None = Field(default=None, max_length=128)

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in ACTION_TYPES:
            raise ValueError("automation.bad_action")
        return v


class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    trigger: str
    enabled: bool = True
    conditions: list[ConditionIn] = Field(default_factory=list, max_length=10)
    actions: list[ActionIn] = Field(min_length=1, max_length=5)

    @field_validator("trigger")
    @classmethod
    def _check_trigger(cls, v: str) -> str:
        if v not in TRIGGERS:
            raise ValueError("automation.bad_trigger")
        return v


class RuleOut(BaseModel):
    id: str
    name: str
    trigger: str
    enabled: bool
    conditions: list[dict]
    actions: list[dict]
    run_count: int
    last_run_at: datetime | None
    last_error: str | None


def _rule_out(r: AutomationRule) -> RuleOut:
    return RuleOut(
        id=str(r.id), name=r.name, trigger=r.trigger, enabled=r.enabled,
        conditions=r.conditions or [], actions=r.actions or [],
        run_count=r.run_count, last_run_at=r.last_run_at, last_error=r.last_error,
    )


async def _get_rule(db: AsyncSession, rule_id: str) -> AutomationRule:
    try:
        rid = uuid.UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "automation.not_found"})
    r = (
        await db.execute(select(AutomationRule).where(AutomationRule.id == rid))
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail={"code": "automation.not_found"})
    return r


@router.get("/triggers")
async def list_triggers(_: User = Depends(require_role("admin"))):
    """Trigger-katalógus: esemény-kulcs → elérhető {{változók}} (a UI súgója)."""
    return {"triggers": TRIGGERS, "ops": list(OPS), "actions": list(ACTION_TYPES)}


@router.get("/telegram-targets")
async def telegram_targets(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))
):
    """A Telegram-bottal összekapcsolt aktív dolgozók — a "Telegram küldése"
    akció címzett-választójához (fő csoport VAGY konkrét dolgozó)."""
    from app.models import Employee

    rows = (
        await db.execute(
            select(Employee).where(
                Employee.telegram_chat_id.is_not(None),
                Employee.status == "active",
            ).order_by(Employee.last_name, Employee.first_name)
        )
    ).scalars().all()
    return [
        {"chat_id": e.telegram_chat_id, "name": f"{e.last_name} {e.first_name}"}
        for e in rows
    ]


@router.get("/rules", response_model=list[RuleOut])
async def list_rules(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_role("admin"))
):
    rows = (
        await db.execute(select(AutomationRule).order_by(AutomationRule.created_at))
    ).scalars().all()
    return [_rule_out(r) for r in rows]


@router.post("/rules", response_model=RuleOut, status_code=201)
async def create_rule(
    body: RuleBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    r = AutomationRule(
        name=body.name, trigger=body.trigger, enabled=body.enabled,
        conditions=[c.model_dump() for c in body.conditions],
        actions=[a.model_dump(exclude_none=True) for a in body.actions],
    )
    db.add(r)
    await db.flush()
    await record_audit(db, actor=actor, action="automation.create", entity_type="automation_rule",
                       entity_id=str(r.id), detail={"name": r.name, "trigger": r.trigger},
                       request=request)
    await db.commit()
    return _rule_out(r)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(
    rule_id: str,
    body: RuleBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    r = await _get_rule(db, rule_id)
    r.name = body.name
    r.trigger = body.trigger
    r.enabled = body.enabled
    r.conditions = [c.model_dump() for c in body.conditions]
    r.actions = [a.model_dump(exclude_none=True) for a in body.actions]
    await record_audit(db, actor=actor, action="automation.update", entity_type="automation_rule",
                       entity_id=str(r.id), detail={"name": r.name}, request=request)
    await db.commit()
    return _rule_out(r)


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    r = await _get_rule(db, rule_id)
    await db.execute(sa_delete(AutomationRule).where(AutomationRule.id == r.id))
    await record_audit(db, actor=actor, action="automation.delete", entity_type="automation_rule",
                       entity_id=rule_id, detail={"name": r.name}, request=request)
    await db.commit()
    return {"ok": True}
