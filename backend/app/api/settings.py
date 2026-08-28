"""Beállítások: SMTP email-fiók (értesítésekhez) és skillek kezelése. Admin only."""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CONFIGURABLE_ROLES,
    FEATURES,
    get_current_user,
    get_permission_matrix,
    record_audit,
    require_role,
)
from app.core.crypto import encrypt_pii
from app.db import get_db
from app.models import (
    BillingoSettings,
    GlsSettings,
    EmailSettings,
    EmployeeSkill,
    PermissionSettings,
    Skill,
    User,
    WorksheetSettings,
)
from app.services.wfm.ai_assign import DEFAULT_ASSIGN_PROMPT
from app.services.wfm.ai_service import (
    PROVIDERS as AI_PROVIDERS,
    get_or_create_settings as get_ai_settings_row,
    test_provider,
)
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


# ─── AI szolgáltatók (Anthropic Claude / Google Gemini) ─────────────────────


class AISettingsBody(BaseModel):
    active_provider: str = "none"  # none | anthropic | gemini
    anthropic_key: str | None = Field(default=None, max_length=256)  # üresen: marad
    anthropic_model: str = Field(default="claude-opus-4-8", max_length=64)
    gemini_key: str | None = Field(default=None, max_length=256)  # üresen: marad
    gemini_model: str = Field(default="gemini-3.5-flash", max_length=64)
    # None vagy üres = beépített alapértelmezett sablon
    assign_prompt: str | None = Field(default=None, max_length=8000)


class AISettingsOut(BaseModel):
    active_provider: str
    has_anthropic_key: bool
    anthropic_model: str
    has_gemini_key: bool
    gemini_model: str
    assign_prompt: str  # a ténylegesen használt sablon (egyedi vagy default)
    assign_prompt_is_custom: bool
    default_assign_prompt: str  # a "visszaállítás" gombhoz


def _ai_out(row) -> AISettingsOut:
    return AISettingsOut(
        active_provider=row.active_provider,
        has_anthropic_key=row.anthropic_key_encrypted is not None,
        anthropic_model=row.anthropic_model,
        has_gemini_key=row.gemini_key_encrypted is not None,
        gemini_model=row.gemini_model,
        assign_prompt=row.assign_prompt or DEFAULT_ASSIGN_PROMPT,
        assign_prompt_is_custom=bool(row.assign_prompt),
        default_assign_prompt=DEFAULT_ASSIGN_PROMPT,
    )


@router.get("/ai", response_model=AISettingsOut)
async def get_ai_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    return _ai_out(await get_ai_settings_row(db))


@router.put("/ai", response_model=AISettingsOut)
async def update_ai_settings(
    body: AISettingsBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    if body.active_provider not in ("none", *AI_PROVIDERS):
        raise HTTPException(status_code=422, detail={"code": "settings.ai_bad_provider"})
    row = await get_ai_settings_row(db)
    row.active_provider = body.active_provider
    row.anthropic_model = body.anthropic_model.strip() or "claude-opus-4-8"
    row.gemini_model = body.gemini_model.strip() or "gemini-3.5-flash"
    if body.anthropic_key:  # üres = nem változik
        row.anthropic_key_encrypted = encrypt_pii(body.anthropic_key.strip())
    if body.gemini_key:
        row.gemini_key_encrypted = encrypt_pii(body.gemini_key.strip())
    # Prompt-sablon: üres vagy a defaulttal azonos → beépített sablon (None)
    prompt = (body.assign_prompt or "").strip()
    row.assign_prompt = prompt if prompt and prompt != DEFAULT_ASSIGN_PROMPT else None
    await record_audit(
        db, actor=actor, action="settings.ai_update", entity_type="settings",
        entity_id="ai", detail={"active_provider": body.active_provider}, request=request,
    )
    await db.commit()
    return _ai_out(row)


class AITestBody(BaseModel):
    provider: str  # anthropic | gemini


@router.post("/ai/test")
async def test_ai_provider(
    body: AITestBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    if body.provider not in AI_PROVIDERS:
        raise HTTPException(status_code=422, detail={"code": "settings.ai_bad_provider"})
    try:
        reply = await test_provider(db, body.provider)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "settings.ai_not_configured"})
    except Exception:
        raise HTTPException(status_code=502, detail={"code": "settings.ai_test_failed"})
    return {"ok": True, "reply": reply[:200]}


# ─── Skillek ─────────────────────────────────────────────────────────────────


class SkillBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class SkillOut(BaseModel):
    id: int
    name: str


@router.get("/skills", response_model=list[SkillOut])
async def list_skills(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),  # ártalmatlan metaadat — bármely belépettnek
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


# ─── Munkalap-PDF testreszabás ───────────────────────────────────────────────


_MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB
_LOGO_MIME = {"image/png", "image/jpeg", "image/jpg"}


class WorksheetSettingsBody(BaseModel):
    company_name: str | None = Field(default=None, max_length=256)
    company_address: str | None = Field(default=None, max_length=512)
    footer_text: str | None = Field(default=None, max_length=512)
    # Ügyfél-munkalap garanciális feltételei és az átvételi elismervény
    # záradéka — üresen a beépített alapszöveg érvényesül.
    customer_footer_text: str | None = Field(default=None, max_length=2000)
    intake_footer_text: str | None = Field(default=None, max_length=2000)
    # Felmérési díj (nettó Ft) — az árajánlat "nem kérem a javítást" opciója.
    survey_fee: float | None = Field(default=None, ge=0, le=10_000_000)
    accent_color: str = Field(default="#1e40af")
    show_materials: bool = True
    show_hours: bool = True
    show_client_signature: bool = True
    show_comments: bool = True
    logo: str | None = None  # data URL (image/png|jpeg); ha üres, marad a régi
    remove_logo: bool = False

    @field_validator("accent_color")
    @classmethod
    def _check_color(cls, v: str) -> str:
        v = v.strip()
        if len(v) != 7 or not v.startswith("#") or any(ch not in "0123456789abcdefABCDEF" for ch in v[1:]):
            raise ValueError("settings.bad_color")
        return v.lower()


class WorksheetSettingsOut(BaseModel):
    company_name: str | None
    company_address: str | None
    footer_text: str | None
    customer_footer_text: str | None
    intake_footer_text: str | None
    # A beépített alapszövegek — a felület placeholderként mutatja őket.
    customer_footer_default: str
    intake_footer_default: str
    survey_fee: float | None
    survey_fee_default: float
    accent_color: str
    show_materials: bool
    show_hours: bool
    show_client_signature: bool
    show_comments: bool
    has_logo: bool


async def _get_or_create_worksheet(db: AsyncSession) -> WorksheetSettings:
    row = (
        await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = WorksheetSettings(id=1)
        db.add(row)
        await db.flush()
    return row


def _worksheet_out(row: WorksheetSettings) -> WorksheetSettingsOut:
    from app.services.wfm.worksheet_pdf import (
        DEFAULT_CUSTOMER_FOOTER,
        DEFAULT_INTAKE_FOOTER,
        DEFAULT_SURVEY_FEE,
    )

    return WorksheetSettingsOut(
        company_name=row.company_name,
        company_address=row.company_address,
        footer_text=row.footer_text,
        customer_footer_text=row.customer_footer_text,
        intake_footer_text=row.intake_footer_text,
        customer_footer_default=DEFAULT_CUSTOMER_FOOTER,
        intake_footer_default=DEFAULT_INTAKE_FOOTER,
        survey_fee=row.survey_fee,
        survey_fee_default=DEFAULT_SURVEY_FEE,
        accent_color=row.accent_color,
        show_materials=row.show_materials,
        show_hours=row.show_hours,
        show_client_signature=row.show_client_signature,
        show_comments=row.show_comments,
        has_logo=row.logo_data is not None,
    )


@router.get("/worksheet", response_model=WorksheetSettingsOut)
async def get_worksheet_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    return _worksheet_out(await _get_or_create_worksheet(db))


@router.get("/worksheet/logo")
async def get_worksheet_logo(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    row = (
        await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))
    ).scalar_one_or_none()
    if row is None or row.logo_data is None:
        raise HTTPException(status_code=404, detail={"code": "settings.no_logo"})
    return Response(content=bytes(row.logo_data), media_type=row.logo_mime or "image/png")


@router.put("/worksheet", response_model=WorksheetSettingsOut)
async def update_worksheet_settings(
    body: WorksheetSettingsBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    row = await _get_or_create_worksheet(db)
    row.company_name = body.company_name
    row.company_address = body.company_address
    row.footer_text = body.footer_text
    row.customer_footer_text = (body.customer_footer_text or "").strip() or None
    row.intake_footer_text = (body.intake_footer_text or "").strip() or None
    row.survey_fee = body.survey_fee
    row.accent_color = body.accent_color
    row.show_materials = body.show_materials
    row.show_hours = body.show_hours
    row.show_client_signature = body.show_client_signature
    row.show_comments = body.show_comments

    if body.remove_logo:
        row.logo_data = None
        row.logo_mime = None
    elif body.logo:
        if not body.logo.startswith("data:"):
            raise HTTPException(status_code=422, detail={"code": "settings.bad_logo"})
        try:
            header, b64 = body.logo.split(",", 1)
            mime = header.split(";")[0].removeprefix("data:").lower()
            raw = base64.b64decode(b64)
        except Exception:
            raise HTTPException(status_code=422, detail={"code": "settings.bad_logo"})
        if mime not in _LOGO_MIME:
            raise HTTPException(status_code=422, detail={"code": "settings.bad_logo"})
        if len(raw) > _MAX_LOGO_BYTES:
            raise HTTPException(status_code=422, detail={"code": "settings.logo_too_large"})
        row.logo_data = raw
        row.logo_mime = "image/jpeg" if mime in ("image/jpg", "image/jpeg") else "image/png"

    await record_audit(
        db, actor=actor, action="settings.worksheet_update", entity_type="settings",
        entity_id="worksheet", request=request,
    )
    await db.commit()
    return _worksheet_out(row)


# ─── Billingó számlázó integráció ────────────────────────────────────────────


class BillingoSettingsBody(BaseModel):
    enabled: bool = False
    # 1. fiók — X-Presso Coffee Kft.
    api_key: str | None = Field(default=None, max_length=256)  # üresen: marad a régi
    block_id: int | None = Field(default=None, ge=1)
    test_mode: bool = True
    # 2. fiók — Premium Caffe Kft.
    pc_api_key: str | None = Field(default=None, max_length=256)
    pc_block_id: int | None = Field(default=None, ge=1)
    pc_test_mode: bool = True


class BillingoSettingsOut(BaseModel):
    enabled: bool
    has_api_key: bool
    block_id: int | None
    test_mode: bool
    pc_has_api_key: bool = False
    pc_block_id: int | None = None
    pc_test_mode: bool = True


async def _get_or_create_billingo(db: AsyncSession) -> BillingoSettings:
    row = (
        await db.execute(select(BillingoSettings).where(BillingoSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = BillingoSettings(id=1)
        db.add(row)
        await db.flush()
    return row


def _billingo_out(row: BillingoSettings) -> BillingoSettingsOut:
    return BillingoSettingsOut(
        enabled=row.enabled,
        has_api_key=row.api_key_encrypted is not None,
        block_id=row.block_id,
        test_mode=row.test_mode,
        pc_has_api_key=row.pc_api_key_encrypted is not None,
        pc_block_id=row.pc_block_id,
        pc_test_mode=row.pc_test_mode,
    )


@router.get("/billingo", response_model=BillingoSettingsOut)
async def get_billingo_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    return _billingo_out(await _get_or_create_billingo(db))


@router.put("/billingo", response_model=BillingoSettingsOut)
async def update_billingo_settings(
    body: BillingoSettingsBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    row = await _get_or_create_billingo(db)
    row.enabled = body.enabled
    row.block_id = body.block_id
    row.test_mode = body.test_mode
    if body.api_key:  # üres = nem változik
        row.api_key_encrypted = encrypt_pii(body.api_key.strip())
    row.pc_block_id = body.pc_block_id
    row.pc_test_mode = body.pc_test_mode
    if body.pc_api_key:  # üres = nem változik
        row.pc_api_key_encrypted = encrypt_pii(body.pc_api_key.strip())
    await record_audit(
        db, actor=actor, action="settings.billingo_update", entity_type="settings",
        entity_id="billingo", detail={"test_mode": body.test_mode}, request=request,
    )
    await db.commit()
    return _billingo_out(row)


# ─── GLS (MyGLS) csomagfeladás ──────────────────────────────────────────────


class GlsSettingsBody(BaseModel):
    username: str | None = Field(default=None, max_length=320)
    password: str | None = Field(default=None, max_length=256)  # üresen: marad a régi
    client_number: str | None = Field(default=None, max_length=32)
    test_mode: bool = True
    printer_type: str = Field(default="A4_2x2", max_length=16)
    sender_name: str | None = Field(default=None, max_length=256)
    sender_zip: str | None = Field(default=None, max_length=16)
    sender_city: str | None = Field(default=None, max_length=128)
    sender_street: str | None = Field(default=None, max_length=256)
    sender_house: str | None = Field(default=None, max_length=32)
    sender_phone: str | None = Field(default=None, max_length=32)
    sender_email: str | None = Field(default=None, max_length=320)


class GlsSettingsOut(BaseModel):
    username: str | None
    has_password: bool
    client_number: str | None
    test_mode: bool
    printer_type: str
    sender_name: str | None
    sender_zip: str | None
    sender_city: str | None
    sender_street: str | None
    sender_house: str | None
    sender_phone: str | None
    sender_email: str | None


async def _get_or_create_gls(db: AsyncSession) -> GlsSettings:
    row = (
        await db.execute(select(GlsSettings).where(GlsSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = GlsSettings(id=1)
        db.add(row)
        await db.flush()
    return row


def _gls_out(row: GlsSettings) -> GlsSettingsOut:
    return GlsSettingsOut(
        username=row.username,
        has_password=row.password_encrypted is not None,
        client_number=row.client_number,
        test_mode=row.test_mode,
        printer_type=row.printer_type,
        sender_name=row.sender_name, sender_zip=row.sender_zip,
        sender_city=row.sender_city, sender_street=row.sender_street,
        sender_house=row.sender_house, sender_phone=row.sender_phone,
        sender_email=row.sender_email,
    )


@router.get("/gls", response_model=GlsSettingsOut)
async def get_gls_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    return _gls_out(await _get_or_create_gls(db))


@router.put("/gls", response_model=GlsSettingsOut)
async def update_gls_settings(
    body: GlsSettingsBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    from app.services.wfm.gls_service import PRINTER_TYPES

    row = await _get_or_create_gls(db)
    row.username = (body.username or "").strip() or None
    row.client_number = (body.client_number or "").strip() or None
    row.test_mode = body.test_mode
    row.printer_type = body.printer_type if body.printer_type in PRINTER_TYPES else "A4_2x2"
    if body.password:  # üres = nem változik; "-" = törlés
        row.password_encrypted = (
            None if body.password.strip() == "-" else encrypt_pii(body.password.strip())
        )
    for field in (
        "sender_name", "sender_zip", "sender_city", "sender_street",
        "sender_house", "sender_phone", "sender_email",
    ):
        setattr(row, field, (getattr(body, field) or "").strip() or None)
    await record_audit(
        db, actor=actor, action="settings.gls_update", entity_type="settings",
        entity_id="gls", detail={"test_mode": body.test_mode}, request=request,
    )
    await db.commit()
    return _gls_out(row)


# ─── Ügyfél-támogatás (QR-oldal tudásbázisa) ────────────────────────────────


class SupportSettingsBody(BaseModel):
    knowledge_base: str | None = Field(default=None, max_length=500_000)
    auto_kb: bool = True


@router.get("/support")
async def get_support_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    from app.models import SupportSettings

    row = (
        await db.execute(select(SupportSettings).where(SupportSettings.id == 1))
    ).scalar_one_or_none()
    return {
        "knowledge_base": row.knowledge_base if row else None,
        "auto_kb": row.auto_kb if row else True,
    }


@router.put("/support")
async def update_support_settings(
    body: SupportSettingsBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    from app.models import SupportSettings

    row = (
        await db.execute(select(SupportSettings).where(SupportSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = SupportSettings(id=1)
        db.add(row)
    row.knowledge_base = body.knowledge_base
    row.auto_kb = body.auto_kb
    await record_audit(
        db, actor=actor, action="settings.support_update", entity_type="settings",
        entity_id="support", detail={"auto_kb": body.auto_kb}, request=request,
    )
    await db.commit()
    return {"knowledge_base": row.knowledge_base, "auto_kb": row.auto_kb}


# ─── Jogosultságok (szerepkör × funkció mátrix) ─────────────────────────────


class PermissionsBody(BaseModel):
    matrix: dict[str, list[str]]


@router.get("/permissions")
async def get_permissions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    return {
        "roles": list(CONFIGURABLE_ROLES),
        "features": list(FEATURES),
        "matrix": await get_permission_matrix(db),
    }


@router.put("/permissions")
async def update_permissions(
    body: PermissionsBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    """A szerepkörönkénti funkció-engedélyek mentése. Az admin nem
    konfigurálható (mindig minden funkciója megvan)."""
    cleaned: dict[str, list[str]] = {}
    for role, features in body.matrix.items():
        if role not in CONFIGURABLE_ROLES:
            raise HTTPException(status_code=422, detail={"code": "settings.bad_role"})
        if not isinstance(features, list):
            raise HTTPException(status_code=422, detail={"code": "settings.bad_matrix"})
        for feature in features:
            if feature not in FEATURES:
                raise HTTPException(status_code=422, detail={"code": "settings.bad_matrix"})
        cleaned[role] = sorted(set(features))

    row = (
        await db.execute(select(PermissionSettings).where(PermissionSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = PermissionSettings(id=1)
        db.add(row)
    # a nem küldött szerepkörök megtartják a jelenlegi (default/mentett) jogaikat
    current = await get_permission_matrix(db)
    current.update(cleaned)
    row.matrix = current
    await record_audit(
        db, actor=actor, action="settings.permissions_update", entity_type="settings",
        entity_id="permissions", request=request,
    )
    await db.commit()
    return {
        "roles": list(CONFIGURABLE_ROLES),
        "features": list(FEATURES),
        "matrix": await get_permission_matrix(db),
    }


# ─── Értesítések ─────────────────────────────────────────────────────────────


class NotificationBody(BaseModel):
    daily_enabled: bool = False
    recipients: str | None = Field(default=None, max_length=2000)
    send_hour: int = Field(default=6, ge=0, le=23)
    weekly_backup: bool = False
    auto_receipt: bool = False
    # WhatsApp (Meta Cloud API)
    wa_enabled: bool = False
    wa_phone_id: str | None = Field(default=None, max_length=64)
    wa_recipients: str | None = Field(default=None, max_length=2000)
    # Token: None/üres = meglévő megtartása; "-" = törlés; egyéb = beállítás.
    wa_token: str | None = Field(default=None, max_length=512)
    # Telegram (Bot API)
    tg_enabled: bool = False
    tg_chat_ids: str | None = Field(default=None, max_length=2000)
    tg_token: str | None = Field(default=None, max_length=512)  # ugyanaz a szemantika
    # Beépített Telegram-értesítések: mely eseményekről menjen üzenet.
    tg_events: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("tg_events")
    @classmethod
    def _check_tg_events(cls, v: list[str]) -> list[str]:
        from app.services.wfm.automation import TRIGGERS

        bad = [x for x in v if x not in TRIGGERS]
        if bad:
            raise ValueError("notifications.bad_tg_event")
        return v


@router.get("/notifications")
async def get_notification_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    from app.services.wfm.notifier import get_or_create_settings as _get

    row = await _get(db)
    await db.commit()
    return {
        "daily_enabled": row.daily_enabled,
        "recipients": row.recipients,
        "send_hour": row.send_hour,
        "weekly_backup": row.weekly_backup,
        "auto_receipt": row.auto_receipt,
        "wa_enabled": row.wa_enabled,
        "wa_phone_id": row.wa_phone_id,
        "wa_recipients": row.wa_recipients,
        "wa_token_set": row.wa_token_encrypted is not None,
        "tg_enabled": row.tg_enabled,
        "tg_chat_ids": row.tg_chat_ids,
        "tg_token_set": row.tg_token_encrypted is not None,
        "tg_events": [
            x.strip() for x in (row.tg_events or "").split(",") if x.strip()
        ],
        "last_daily_sent": row.last_daily_sent,
        "last_backup_sent": row.last_backup_sent,
    }


@router.put("/notifications")
async def update_notification_settings(
    body: NotificationBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    from app.services.wfm.notifier import get_or_create_settings as _get

    row = await _get(db)
    row.daily_enabled = body.daily_enabled
    row.recipients = (body.recipients or "").strip() or None
    row.send_hour = body.send_hour
    row.weekly_backup = body.weekly_backup
    row.auto_receipt = body.auto_receipt
    row.wa_enabled = body.wa_enabled
    row.wa_phone_id = (body.wa_phone_id or "").strip() or None
    row.wa_recipients = (body.wa_recipients or "").strip() or None
    row.tg_enabled = body.tg_enabled
    row.tg_chat_ids = (body.tg_chat_ids or "").strip() or None
    row.tg_events = ",".join(body.tg_events) or None
    if body.wa_token or body.tg_token:
        from app.core.crypto import encrypt_pii

        if body.wa_token:
            row.wa_token_encrypted = (
                None if body.wa_token.strip() == "-" else encrypt_pii(body.wa_token.strip())
            )
        if body.tg_token:
            row.tg_token_encrypted = (
                None if body.tg_token.strip() == "-" else encrypt_pii(body.tg_token.strip())
            )
    await record_audit(
        db, actor=actor, action="settings.notifications_update", entity_type="settings",
        entity_id="notifications", request=request,
    )
    await db.commit()
    return {"ok": True}


# ─── Munkarend (ünnep-áthelyezések) és Mt.-figyelés ─────────────────────────


class CalendarBody(BaseModel):
    rest_days: list[str] = Field(default_factory=list, max_length=31)
    worked_saturdays: list[str] = Field(default_factory=list, max_length=31)
    note: str | None = Field(default=None, max_length=512)


@router.get("/calendar/{year}")
async def get_calendar_year(
    year: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """Az adott év munkarendje: beégetett + DB-beli adatok, forrással."""
    from sqlalchemy import select as sa_select

    from app.models import CalendarOverride
    from app.services.wfm.holidays import (
        REST_DAY_OVERRIDES, WORKED_SATURDAYS, public_holidays,
    )

    row = (
        await db.execute(
            sa_select(CalendarOverride).where(CalendarOverride.year == year)
        )
    ).scalar_one_or_none()
    builtin_rest = sorted(str(d) for d in REST_DAY_OVERRIDES.get(year, set()))
    builtin_sat = sorted(str(d) for d in WORKED_SATURDAYS.get(year, set()))
    from app.services.wfm.notifier import get_or_create_settings as _get

    notif = await _get(db)
    await db.commit()
    return {
        "year": year,
        "holidays": sorted(str(d) for d in public_holidays(year)),
        "builtin_rest_days": builtin_rest,
        "builtin_worked_saturdays": builtin_sat,
        "rest_days": row.rest_days if row else [],
        "worked_saturdays": row.worked_saturdays if row else [],
        "source": row.source if row else None,
        "note": row.note if row else None,
        "updated_at": row.updated_at.isoformat() if row else None,
        "cal_last_check": notif.cal_last_check,
        "mt_last_check": notif.mt_last_check,
        "mt_last_result": notif.mt_last_result,
    }


@router.put("/calendar/{year}")
async def put_calendar_year(
    year: int,
    body: CalendarBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    """Az év munkarendjének kézi rögzítése/javítása (source='manual')."""
    from datetime import date as date_cls

    from sqlalchemy import select as sa_select

    from app.models import CalendarOverride
    from app.services.wfm.holidays import load_overrides

    try:
        rest = [str(date_cls.fromisoformat(x)) for x in body.rest_days]
        sat = [str(date_cls.fromisoformat(x)) for x in body.worked_saturdays]
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "calendar.bad_date"})
    if any(not x.startswith(str(year)) for x in rest + sat):
        raise HTTPException(status_code=422, detail={"code": "calendar.wrong_year"})
    if any(date_cls.fromisoformat(x).weekday() != 5 for x in sat):
        raise HTTPException(status_code=422, detail={"code": "calendar.not_saturday"})

    row = (
        await db.execute(
            sa_select(CalendarOverride).where(CalendarOverride.year == year)
        )
    ).scalar_one_or_none()
    if row is None:
        row = CalendarOverride(year=year)
        db.add(row)
    row.rest_days = rest
    row.worked_saturdays = sat
    row.source = "manual"
    row.note = body.note
    await record_audit(
        db, actor=actor, action="settings.calendar_update", entity_type="settings",
        entity_id=f"calendar-{year}",
        detail={"rest_days": rest, "worked_saturdays": sat}, request=request,
    )
    await db.commit()
    await load_overrides(db)
    return {"ok": True}


@router.post("/calendar/refresh")
async def refresh_calendar_now(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """A hiányzó évek munkarendjének azonnali AI-lekérése (a havi őr
    átugrásával)."""
    from app.services.wfm.calendar_watch import ensure_next_year_calendar
    from app.services.wfm.notifier import get_or_create_settings as _get

    row = await _get(db)
    row.cal_last_check = None  # az őr átugrása kézi futtatásnál
    await db.commit()
    try:
        added = await ensure_next_year_calendar(db)
    except Exception:
        raise HTTPException(status_code=502, detail={"code": "calendar.ai_failed"})
    return {"ok": True, "added": added}


@router.post("/mt-check")
async def run_mt_check_now(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """Azonnali Mt.-változás-ellenőrzés (a havi őr átugrásával)."""
    from app.services.wfm.calendar_watch import monthly_mt_check
    from app.services.wfm.notifier import get_or_create_settings as _get

    row = await _get(db)
    row.mt_last_check = None
    await db.commit()
    try:
        ran = await monthly_mt_check(db)
    except Exception:
        raise HTTPException(status_code=502, detail={"code": "calendar.ai_failed"})
    row = await _get(db)
    result = row.mt_last_result
    await db.commit()
    if not ran:
        raise HTTPException(status_code=422, detail={"code": "settings.ai_not_configured"})
    return {"ok": True, "result": result}


class WhatsAppTestBody(BaseModel):
    to: str | None = None  # None = az első beállított WhatsApp-címzett


@router.post("/notifications/whatsapp-test")
async def send_whatsapp_test(
    body: WhatsAppTestBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """Próba-üzenet a WhatsApp-bekötés ellenőrzésére."""
    from app.services.wfm.whatsapp import load_whatsapp_config, send_whatsapp

    config = await load_whatsapp_config(db)
    await db.commit()
    if config is None:
        raise HTTPException(status_code=422, detail={"code": "whatsapp.not_configured"})
    to = body.to or (config["recipients"][0] if config["recipients"] else None)
    if not to:
        raise HTTPException(status_code=422, detail={"code": "whatsapp.no_recipient"})
    ok = await send_whatsapp(config, to, "Iwfm próba-üzenet — a WhatsApp-bekötés működik. ✅")
    if not ok:
        raise HTTPException(status_code=422, detail={"code": "whatsapp.send_failed"})
    return {"ok": True, "to": to}


class TelegramTestBody(BaseModel):
    chat_id: str | None = None  # None = az első beállított chat


@router.post("/notifications/telegram-test")
async def send_telegram_test(
    body: TelegramTestBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """Próba-üzenet a Telegram-bekötés ellenőrzésére."""
    from app.services.wfm.telegram import load_telegram_config, send_telegram

    config = await load_telegram_config(db)
    await db.commit()
    if config is None:
        raise HTTPException(status_code=422, detail={"code": "telegram.not_configured"})
    chat = body.chat_id or (config["chat_ids"][0] if config["chat_ids"] else None)
    if not chat:
        raise HTTPException(status_code=422, detail={"code": "telegram.no_recipient"})
    ok = await send_telegram(config, chat, "Iwfm próba-üzenet — a Telegram-bekötés működik. ✅")
    if not ok:
        raise HTTPException(status_code=422, detail={"code": "telegram.send_failed"})
    return {"ok": True, "chat_id": chat}


@router.post("/notifications/test")
async def send_test_digest(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """Próba: a napi összefoglaló azonnali kiküldése a címzetteknek."""
    from app.services.wfm.email_service import load_smtp_config, send_email
    from app.services.wfm.notifier import (
        _recipients,
        build_daily_digest,
        get_or_create_settings as _get,
    )

    row = await _get(db)
    recipients = _recipients(row)
    if not recipients:
        raise HTTPException(status_code=422, detail={"code": "settings.no_recipients"})
    smtp = await load_smtp_config(db)
    if smtp is None:
        raise HTTPException(status_code=422, detail={"code": "settings.smtp_not_configured"})
    body = await build_daily_digest(db)
    sent = 0
    for to in recipients:
        if await send_email(smtp, to, "Iwfm napi összefoglaló (próba)", body):
            sent += 1
    return {"sent": sent}


@router.post("/billingo/test")
async def test_billingo(
    company: str | None = Query(default=None),  # xp (alap) | pc
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """Kapcsolat-teszt: számlatömbök lekérése (block_id kiválasztásához is)."""
    from app.services.wfm.billingo_service import test_connection

    try:
        return await test_connection(db, company)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "settings.billingo_not_configured"})
    except Exception:
        raise HTTPException(status_code=502, detail={"code": "settings.billingo_test_failed"})
