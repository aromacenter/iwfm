"""Levélküldés (158. kör): egyedi e-mail EGY címre vagy MINDEN aktív partnernek,
akinél e-mail cím van rögzítve. A cég SMTP-beállításával megy (Beállítások →
E-mail), címzettenként külön levélben (a címek nem látják egymást), háttérben,
kampány-naplóval (hány címzett, hány ment el, kik buktak). Csak admin."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, record_audit
from app.db import get_db, get_session_factory
from app.models import MailCampaign, Partner, User, WorksheetSettings
from app.services.wfm import email_service
from app.services.wfm.license import require_module

router = APIRouter(dependencies=[Depends(require_module("mailing"))])
logger = logging.getLogger(__name__)
MAX_RECIPIENTS = 3000


def _admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "auth.admin_only"})
    return user


class Recipient(BaseModel):
    name: str
    email: str


class RecipientsOut(BaseModel):
    count: int
    sample: list[Recipient]
    smtp_ready: bool
    sender: str | None


class SendBody(BaseModel):
    mode: str = Field(pattern="^(single|partners)$")
    to: str | None = Field(default=None, max_length=320)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)


class CampaignOut(BaseModel):
    id: str
    subject: str
    mode: str
    to_address: str | None
    recipient_count: int
    sent_count: int
    failed_count: int
    failed: list[str]
    status: str
    created_by_name: str
    created_at: datetime
    finished_at: datetime | None


async def _partner_recipients(db: AsyncSession) -> list[Recipient]:
    """Aktív partnerek e-mail címei (egy partnernél több cím is lehet), címenként
    egyszer — a sorrend a partner neve szerint."""
    rows = (
        await db.execute(select(Partner).where(Partner.is_active.is_(True)).order_by(Partner.name))
    ).scalars().all()
    seen: set[str] = set()
    out: list[Recipient] = []
    for p in rows:
        for addr in email_service.split_addresses(p.contact_email):
            key = addr.lower()
            if "@" not in addr or key in seen:
                continue
            seen.add(key)
            out.append(Recipient(name=p.name, email=addr))
    return out


async def _signature(db: AsyncSession) -> str:
    row = (await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))).scalar_one_or_none()
    company = (row.company_name if row else None) or ""
    return f"\n\n--\n{company}" if company else ""


def _out(c: MailCampaign) -> CampaignOut:
    return CampaignOut(
        id=str(c.id), subject=c.subject, mode=c.mode, to_address=c.to_address,
        recipient_count=c.recipient_count, sent_count=c.sent_count, failed_count=c.failed_count,
        failed=list(c.failed or []), status=c.status, created_by_name=c.created_by_name,
        created_at=c.created_at, finished_at=c.finished_at,
    )


@router.get("/recipients", response_model=RecipientsOut)
async def recipients(db: AsyncSession = Depends(get_db), _: User = Depends(_admin)):
    recs = await _partner_recipients(db)
    smtp = await email_service.load_smtp_config(db)
    return RecipientsOut(count=len(recs), sample=recs[:50], smtp_ready=smtp is not None,
                         sender=smtp.from_address if smtp else None)


@router.get("/campaigns", response_model=list[CampaignOut])
async def campaigns(db: AsyncSession = Depends(get_db), _: User = Depends(_admin)):
    rows = (
        await db.execute(select(MailCampaign).order_by(MailCampaign.created_at.desc()).limit(100))
    ).scalars().all()
    return [_out(c) for c in rows]


async def _run_campaign(campaign_id: uuid.UUID, config, targets: list[tuple[str, str]], subject: str, body: str) -> None:
    """Háttérben: címzettenként egy levél; a végén a kampány-sor lezárul."""
    sent, failed = 0, []
    for name, addr in targets:
        ok = await email_service.send_email(config, addr, subject, body)
        if ok:
            sent += 1
        else:
            failed.append(addr)
    factory = get_session_factory()
    async with factory() as session:
        c = (await session.execute(select(MailCampaign).where(MailCampaign.id == campaign_id))).scalar_one_or_none()
        if c is not None:
            c.sent_count, c.failed_count, c.failed = sent, len(failed), failed
            c.status, c.finished_at = "done", datetime.now(UTC)
            await session.commit()


@router.post("/test")
async def send_test(body: SendBody, db: AsyncSession = Depends(get_db), actor: User = Depends(_admin)):
    """Próbalevél a bejelentkezett admin saját e-mail címére (ugyanazzal a szöveggel)."""
    if not actor.email or "@" not in actor.email:
        raise HTTPException(status_code=422, detail={"code": "mailing.no_own_email"})
    config = await email_service.load_smtp_config(db)
    if config is None:
        raise HTTPException(status_code=422, detail={"code": "settings.email_not_configured"})
    ok = await email_service.send_email(config, actor.email, f"[PRÓBA] {body.subject}", body.body + await _signature(db))
    if not ok:
        raise HTTPException(status_code=502, detail={"code": "settings.email_send_failed"})
    return {"ok": True, "to": actor.email}


@router.post("/send", response_model=CampaignOut, status_code=201)
async def send(
    body: SendBody,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(_admin),
):
    config = await email_service.load_smtp_config(db)
    if config is None:
        raise HTTPException(status_code=422, detail={"code": "settings.email_not_configured"})
    text = body.body.strip() + await _signature(db)
    if body.mode == "single":
        addrs = [a for a in email_service.split_addresses(body.to) if "@" in a]
        if not addrs:
            raise HTTPException(status_code=422, detail={"code": "mailing.bad_address"})
        targets = [("", a) for a in addrs]
    else:
        targets = [(r.name, r.email) for r in await _partner_recipients(db)]
        if not targets:
            raise HTTPException(status_code=422, detail={"code": "mailing.no_recipients"})
    if len(targets) > MAX_RECIPIENTS:
        raise HTTPException(status_code=422, detail={"code": "mailing.too_many"})

    campaign = MailCampaign(
        subject=body.subject.strip(), body=body.body.strip(), mode=body.mode,
        to_address=(body.to or "").strip() or None if body.mode == "single" else None,
        recipient_count=len(targets), sent_count=0, failed_count=0, failed=[],
        status="running", created_by=actor.id, created_by_name=actor.display_name,
    )
    db.add(campaign)
    await record_audit(
        db, actor=actor, action="mailing.send", entity_type="mail_campaign",
        detail={"mode": body.mode, "recipients": len(targets), "subject": body.subject[:120]}, request=request,
    )
    await db.commit()
    await db.refresh(campaign)
    if body.mode == "single":
        # egy (vagy pár) cím: azonnal, a válasz már az eredményt hozza
        await _run_campaign(campaign.id, config, targets, campaign.subject, text)
        await db.refresh(campaign)
        return _out(campaign)
    background_tasks.add_task(_run_campaign, campaign.id, config, targets, campaign.subject, text)
    return _out(campaign)
