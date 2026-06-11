"""Beállítások: SMTP email-fiók (értesítésekhez) és skillek kezelése. Admin only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_role
from app.core.crypto import encrypt_pii
from app.db import get_db
from app.models import EmailSettings, EmployeeSkill, Skill, User
from app.services.wfm.email_service import load_smtp_config, send_email

router = APIRouter()


# ─── Email (SMTP) ────────────────────────────────────────────────────────────


class EmailSettingsBody(BaseModel):
    enabled: bool = False
    host: str | None = Field(default=None, max_length=256)
    port: int = Field(default=587, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=256)  # üresen: marad a régi
    from_address: EmailStr | None = None
    use_tls: bool = True


class EmailSettingsOut(BaseModel):
    enabled: bool
    host: str | None
    port: int
    username: str | None
    has_password: bool
    from_address: str | None
    use_tls: bool


async def _get_or_create_settings(db: AsyncSession) -> EmailSettings:
    row = (
        await db.execute(select(EmailSettings).where(EmailSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = EmailSettings(id=1)
        db.add(row)
        await db.flush()
    return row


def _settings_out(row: EmailSettings) -> EmailSettingsOut:
    return EmailSettingsOut(
        enabled=row.enabled,
        host=row.host,
        port=row.port,
        username=row.username,
        has_password=row.password_encrypted is not None,
        from_address=row.from_address,
        use_tls=row.use_tls,
    )


@router.get("/email", response_model=EmailSettingsOut)
async def get_email_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    return _settings_out(await _get_or_create_settings(db))


@router.put("/email", response_model=EmailSettingsOut)
async def update_email_settings(
    body: EmailSettingsBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    row = await _get_or_create_settings(db)
    row.enabled = body.enabled
    row.host = body.host
    row.port = body.port
    row.username = body.username
    row.from_address = body.from_address
    row.use_tls = body.use_tls
    if body.password:  # üres jelszó = nem változik
        row.password_encrypted = encrypt_pii(body.password)
    await record_audit(
        db, actor=actor, action="settings.email_update", entity_type="settings",
        entity_id="email", request=request,
    )
    await db.commit()
    return _settings_out(row)


class TestEmailBody(BaseModel):
    to: EmailStr


@router.post("/email/test")
async def test_email(
    body: TestEmailBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    config = await load_smtp_config(db)
    if config is None:
        raise HTTPException(status_code=422, detail={"code": "settings.email_not_configured"})
    ok = await send_email(
        config,
        body.to,
        "Iwfm — teszt email",
        "Ez egy teszt üzenet az Iwfm munkaerő-kezelő rendszerből.\n"
        "Ha ezt látod, az email-értesítések működnek. ✓",
    )
    if not ok:
        raise HTTPException(status_code=502, detail={"code": "settings.email_send_failed"})
    return {"ok": True}


# ─── Skillek ─────────────────────────────────────────────────────────────────


class SkillBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class SkillOut(BaseModel):
    id: int
    name: str


@router.get("/skills", response_model=list[SkillOut])
async def list_skills(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    rows = (await db.execute(select(Skill).order_by(Skill.name))).scalars()
    return [SkillOut(id=s.id, name=s.name) for s in rows]


@router.post("/skills", response_model=SkillOut, status_code=201)
async def create_skill(
    body: SkillBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    existing = (
        await db.execute(select(Skill).where(Skill.name == body.name.strip()))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail={"code": "skills.name_taken"})
    skill = Skill(name=body.name.strip())
    db.add(skill)
    await db.flush()
    await record_audit(
        db, actor=actor, action="skills.create", entity_type="skill",
        entity_id=str(skill.id), request=request,
    )
    await db.commit()
    return SkillOut(id=skill.id, name=skill.name)


@router.delete("/skills/{skill_id}")
async def delete_skill(
    skill_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if skill is None:
        raise HTTPException(status_code=404, detail={"code": "skills.not_found"})
    await db.execute(delete(EmployeeSkill).where(EmployeeSkill.skill_id == skill_id))
    await db.delete(skill)
    await record_audit(
        db, actor=actor, action="skills.delete", entity_type="skill",
        entity_id=str(skill_id), request=request,
    )
    await db.commit()
    return {"ok": True}
