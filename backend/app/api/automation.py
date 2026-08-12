"""E-mail sablonok + automatizálási szabályok (Flow-szerű) — admin."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_role
from app.db import get_db
from app.models import AutomationRule, EmailTemplate, User
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
