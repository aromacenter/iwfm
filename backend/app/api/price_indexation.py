"""Áremelés / indexálás (184. kör): előnézet, ütemezés, értesítő levél,
alkalmazás, visszavonás. Csak admin; modul-kapcsoló: price_indexation."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, record_audit
from app.db import get_session_factory
from app.models import MailCampaign, Partner, PriceIndexation, User, WorksheetSettings
from app.services.wfm import email_service
from app.services.wfm.license import require_module
from app.services.wfm.price_indexation import (
    TARGETS,
    apply_indexation,
    compute_rows,
    partner_table,
    render_notice,
    revert_indexation,
)

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_module("price_indexation"))])


def _admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "auth.admin_only"})
    return user


class PreviewBody(BaseModel):
    partner_ids: list[str] | None = None  # None/üres = minden aktív partner
    targets: list[str] = Field(default_factory=lambda: list(TARGETS), max_length=10)
    percent: float | None = Field(default=None, ge=-90, le=500)
    amount: float | None = Field(default=None, ge=-1_000_000, le=10_000_000)
    round_to: int = Field(default=1, ge=1, le=1000)
    effective_from: date

    @model_validator(mode="after")
    def _one_of(self):
        if not self.percent and not self.amount:
            raise ValueError("percent or amount required")
        if not [t for t in self.targets if t in TARGETS]:
            raise ValueError("targets required")
        return self


class CreateBody(PreviewBody):
    name: str = Field(min_length=2, max_length=128)


class NoticeBody(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)


class RowOut(BaseModel):
    partner_id: str
    partner: str
    kind: str
    ref: str
    label: str
    old: float
    new: float
    applied: bool | None = None


class PreviewOut(BaseModel):
    rows: list[RowOut]
    partners: int
    items: int
    partners_without_email: list[str]


class IndexationOut(BaseModel):
    id: str
    name: str
    percent: float | None
    amount: float | None
    round_to: int
    effective_from: date
    targets: list[str]
    partner_ids: list[str] | None
    status: str
    rows: list[RowOut]
    partners: int
    items: int
    notice_sent_at: datetime | None
    notice_count: int
    applied_at: datetime | None
    reverted_at: datetime | None
    created_by_name: str
    created_at: datetime


def _out(ix: PriceIndexation) -> IndexationOut:
    rows = [RowOut(**r) for r in (ix.rows or [])]
    return IndexationOut(
        id=str(ix.id), name=ix.name, percent=ix.percent, amount=ix.amount, round_to=ix.round_to,
        effective_from=ix.effective_from, targets=list(ix.targets or []), partner_ids=ix.partner_ids,
        status=ix.status, rows=rows, partners=len({r.partner_id for r in rows}), items=len(rows),
        notice_sent_at=ix.notice_sent_at, notice_count=ix.notice_count or 0, applied_at=ix.applied_at,
        reverted_at=ix.reverted_at, created_by_name=ix.created_by_name or "", created_at=ix.created_at,
    )


async def _get(db: AsyncSession, ix_id: str) -> PriceIndexation:
    try:
        uid = uuid.UUID(ix_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail={"code": "price.not_found"}) from e
    ix = await db.get(PriceIndexation, uid)
    if ix is None:
        raise HTTPException(status_code=404, detail={"code": "price.not_found"})
    return ix


async def _no_email(db: AsyncSession, rows: list[dict]) -> list[str]:
    pids = {r["partner_id"] for r in rows}
    if not pids:
        return []
    ids = [uuid.UUID(p) for p in pids]
    out = []
    for p in (await db.execute(select(Partner).where(Partner.id.in_(ids)))).scalars().all():
        if not [a for a in email_service.split_addresses(p.contact_email) if "@" in a]:
            out.append(p.name)
    return sorted(out)


@router.post("/preview", response_model=PreviewOut)
async def preview(body: PreviewBody, db: AsyncSession = Depends(get_db), _: User = Depends(_admin)):
    rows = await compute_rows(
        db, partner_ids=body.partner_ids or None, targets=body.targets, percent=body.percent,
        amount=body.amount, round_to=body.round_to, effective_from=body.effective_from,
    )
    return PreviewOut(rows=[RowOut(**r) for r in rows], partners=len({r["partner_id"] for r in rows}),
                      items=len(rows), partners_without_email=await _no_email(db, rows))


@router.get("", response_model=list[IndexationOut])
async def list_indexations(db: AsyncSession = Depends(get_db), _: User = Depends(_admin)):
    rows = (await db.execute(select(PriceIndexation).order_by(PriceIndexation.created_at.desc()).limit(100))).scalars().all()
    return [_out(x) for x in rows]


@router.post("", response_model=IndexationOut, status_code=201)
async def create(body: CreateBody, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(_admin)):
    rows = await compute_rows(
        db, partner_ids=body.partner_ids or None, targets=body.targets, percent=body.percent,
        amount=body.amount, round_to=body.round_to, effective_from=body.effective_from,
    )
    if not rows:
        raise HTTPException(status_code=422, detail={"code": "price.nothing_to_change"})
    ix = PriceIndexation(
        name=body.name.strip(), percent=body.percent, amount=body.amount, round_to=body.round_to,
        effective_from=body.effective_from, targets=[t for t in body.targets if t in TARGETS],
        partner_ids=body.partner_ids or None, status="scheduled", rows=rows,
        created_by=actor.id, created_by_name=actor.display_name,
    )
    db.add(ix)
    await record_audit(
        db, actor=actor, action="price.indexation_scheduled", entity_type="price_indexation",
        detail={"name": ix.name, "items": len(rows), "percent": body.percent, "amount": body.amount,
                "effective_from": body.effective_from.isoformat()}, request=request,
    )
    await db.commit()
    await db.refresh(ix)
    if ix.effective_from <= date.today():
        await apply_indexation(db, ix, actor_id=actor.id)
        await db.refresh(ix)
    return _out(ix)


@router.get("/{ix_id}", response_model=IndexationOut)
async def get_one(ix_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(_admin)):
    return _out(await _get(db, ix_id))


@router.post("/{ix_id}/apply", response_model=IndexationOut)
async def apply_now(ix_id: str, db: AsyncSession = Depends(get_db), actor: User = Depends(_admin)):
    ix = await _get(db, ix_id)
    if ix.status != "scheduled":
        raise HTTPException(status_code=409, detail={"code": "price.not_scheduled"})
    await apply_indexation(db, ix, actor_id=actor.id)
    await db.refresh(ix)
    return _out(ix)


@router.post("/{ix_id}/revert", response_model=IndexationOut)
async def revert(ix_id: str, db: AsyncSession = Depends(get_db), actor: User = Depends(_admin)):
    ix = await _get(db, ix_id)
    if ix.status != "applied":
        raise HTTPException(status_code=409, detail={"code": "price.not_applied"})
    await revert_indexation(db, ix, actor_id=actor.id)
    await db.refresh(ix)
    return _out(ix)


@router.delete("/{ix_id}", status_code=204)
async def cancel(ix_id: str, request: Request, db: AsyncSession = Depends(get_db), actor: User = Depends(_admin)):
    ix = await _get(db, ix_id)
    if ix.status != "scheduled":
        raise HTTPException(status_code=409, detail={"code": "price.not_scheduled"})
    ix.status = "cancelled"
    await record_audit(db, actor=actor, action="price.indexation_cancelled", entity_type="price_indexation",
                       entity_id=str(ix.id), detail={"name": ix.name}, request=request)
    await db.commit()


async def _company_name(db: AsyncSession) -> str:
    row = (await db.execute(select(WorksheetSettings).where(WorksheetSettings.id == 1))).scalar_one_or_none()
    return (row.company_name if row else None) or ""


async def _send_notices(campaign_id: uuid.UUID, ix_id: uuid.UUID, config, letters: list[tuple[str, str, str]]) -> None:
    sent, failed = 0, []
    for addr, subject, text in letters:
        if await email_service.send_email(config, addr, subject, text):
            sent += 1
        else:
            failed.append(addr)
    async with get_session_factory()() as session:
        c = await session.get(MailCampaign, campaign_id)
        if c is not None:
            c.sent_count, c.failed_count, c.failed = sent, len(failed), failed
            c.status, c.finished_at = "done", datetime.now(UTC)
        ix = await session.get(PriceIndexation, ix_id)
        if ix is not None:
            ix.notice_count = (ix.notice_count or 0) + sent
        await session.commit()


@router.post("/{ix_id}/notice-preview")
async def notice_preview(ix_id: str, body: NoticeBody, db: AsyncSession = Depends(get_db), _: User = Depends(_admin)):
    """Az első e-mailes partner levele kitöltve — ellenőrzéshez."""
    ix = await _get(db, ix_id)
    rows = list(ix.rows or [])
    company = await _company_name(db)
    for r in rows:
        p = await db.get(Partner, uuid.UUID(r["partner_id"]))
        if p is None or not [a for a in email_service.split_addresses(p.contact_email) if "@" in a]:
            continue
        text = render_notice(body.body, partner_name=p.name, ix=ix, company=company, table=partner_table(rows, r["partner_id"]))
        return {"partner": p.name, "subject": render_notice(body.subject, partner_name=p.name, ix=ix, company=company, table=""), "body": text}
    raise HTTPException(status_code=422, detail={"code": "mailing.no_recipients"})


@router.post("/{ix_id}/notify")
async def notify(
    ix_id: str, body: NoticeBody, request: Request, background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db), actor: User = Depends(_admin),
):
    """Értesítő levél minden érintett, e-mailes partnernek — partnerenként a
    saját tételeivel ({{tabla}}). A Levélküldés naplójában is megjelenik."""
    ix = await _get(db, ix_id)
    if ix.status not in ("scheduled", "applied"):
        raise HTTPException(status_code=409, detail={"code": "price.not_scheduled"})
    config = await email_service.load_smtp_config(db)
    if config is None:
        raise HTTPException(status_code=422, detail={"code": "settings.email_not_configured"})
    rows = list(ix.rows or [])
    company = await _company_name(db)
    letters: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for pid in sorted({r["partner_id"] for r in rows}):
        p = await db.get(Partner, uuid.UUID(pid))
        if p is None:
            continue
        addrs = [a for a in email_service.split_addresses(p.contact_email) if "@" in a]
        table = partner_table(rows, pid)
        subject = render_notice(body.subject, partner_name=p.name, ix=ix, company=company, table="")
        text = render_notice(body.body, partner_name=p.name, ix=ix, company=company, table=table)
        if company:
            text += f"\n\n--\n{company}"
        for a in addrs:
            if a.lower() in seen:
                continue
            seen.add(a.lower())
            letters.append((a, subject, text))
    if not letters:
        raise HTTPException(status_code=422, detail={"code": "mailing.no_recipients"})
    campaign = MailCampaign(
        subject=body.subject.strip()[:200], body=body.body.strip(), mode="partners", to_address=None,
        recipient_count=len(letters), sent_count=0, failed_count=0, failed=[], status="running",
        created_by=actor.id, created_by_name=actor.display_name, is_html=False, attachment_names=[],
    )
    db.add(campaign)
    ix.notice_sent_at = datetime.now(UTC)
    await record_audit(
        db, actor=actor, action="price.indexation_notified", entity_type="price_indexation",
        entity_id=str(ix.id), detail={"name": ix.name, "recipients": len(letters), "subject": body.subject[:120]}, request=request,
    )
    await db.commit()
    await db.refresh(campaign)
    background_tasks.add_task(_send_notices, campaign.id, ix.id, config, letters)
    return {"ok": True, "recipients": len(letters), "campaign_id": str(campaign.id)}
