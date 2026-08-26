"""Godex címkenyomtatás nyomtatási soron át.

A felhő-backend nem éri el az üzleti hálózaton lévő Godex címkenyomtatót,
ezért a nyomtatás két lépésben történik: a felület a kiválasztott gépek
EZPL-címkéit egy nyomtatási sorba teszi, az üzleti PC-n futó nyomtató-ügynök
(agent/print_agent.ps1) pedig pár másodpercenként lekéri a várakozó
feladatokat és raw módban a nyomtató 9100-as portjára küldi őket.

Az ügynök hitelesítése a Beállításokban generálható kulccsal történik
(X-Agent-Key fejléc); minden lekérés frissíti az "utoljára jelentkezett"
időt, ebből látszik a felületen, hogy az ügynök online-e.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm, require_role
from app.db import get_db
from app.models import Asset, PrintJob, PrintSettings, User

router = APIRouter()  # /api/print-jobs (belső felület)
agent_router = APIRouter()  # /api/print-agent (a helyi ügynök, kulccsal)

AGENT_ONLINE_SECONDS = 90  # ennyin belüli jelentkezés számít online-nak


async def _get_or_create_settings(db: AsyncSession) -> PrintSettings:
    row = (
        await db.execute(select(PrintSettings).where(PrintSettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = PrintSettings(id=1)
        db.add(row)
        await db.flush()
    return row


# ─── Belső felület ───────────────────────────────────────────────────────────


class EnqueueBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


class JobOut(BaseModel):
    id: str
    label: str | None
    status: str
    error: str | None
    created_at: datetime
    printed_at: datetime | None


class QueueOut(BaseModel):
    jobs: list[JobOut]
    agent_configured: bool
    agent_online: bool
    agent_last_seen: datetime | None


def _job_out(j: PrintJob) -> JobOut:
    return JobOut(
        id=str(j.id), label=j.label, status=j.status, error=j.error,
        created_at=j.created_at, printed_at=j.printed_at,
    )


@router.post("", response_model=JobOut)
async def enqueue_labels(
    body: EnqueueBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("machines")),
):
    """A kiválasztott gépek QR-címkéi a Godex nyomtatási sorába."""
    from app.api.support import _label_items
    from app.services.wfm.qr_label import build_qr_labels_ezpl

    ids = []
    for raw in body.ids:
        try:
            ids.append(uuid.UUID(raw))
        except ValueError:
            continue
    assets = (
        await db.execute(select(Asset).where(Asset.id.in_(ids)).order_by(Asset.barcode))
    ).scalars().all()
    if not assets:
        raise HTTPException(status_code=404, detail={"code": "asset.not_found"})

    items = await _label_items(db, assets)
    first = str(assets[0].barcode or "")
    label = f"QR-címke ×{len(items)}" + (f" ({first}…)" if len(items) > 1 else f" ({first})")
    job = PrintJob(
        kind="qr_label",
        label=label,
        payload=build_qr_labels_ezpl(items).decode("ascii"),
        created_by=actor.id,
    )
    db.add(job)
    await record_audit(
        db, actor=actor, action="print.enqueue", entity_type="print_job",
        detail={"count": len(items)}, request=request,
    )
    await db.commit()
    await db.refresh(job)
    return _job_out(job)


@router.get("", response_model=QueueOut)
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("machines")),
):
    """A legutóbbi nyomtatási feladatok + az ügynök állapota."""
    jobs = (
        await db.execute(
            select(PrintJob).order_by(PrintJob.created_at.desc()).limit(20)
        )
    ).scalars().all()
    st = await _get_or_create_settings(db)
    await db.commit()
    last = st.agent_last_seen
    online = False
    if last is not None:
        ref = last if last.tzinfo else last.replace(tzinfo=UTC)
        online = datetime.now(UTC) - ref < timedelta(seconds=AGENT_ONLINE_SECONDS)
    return QueueOut(
        jobs=[_job_out(j) for j in jobs],
        agent_configured=st.agent_key is not None,
        agent_online=online,
        agent_last_seen=last,
    )


@router.post("/agent-key")
async def generate_agent_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    """Új ügynök-kulcs generálása (a régit érvényteleníti). A kulcs csak most
    jelenik meg — ezt kell a helyi ügynök konfigurációjába másolni."""
    st = await _get_or_create_settings(db)
    key = secrets.token_urlsafe(24)
    st.agent_key = key
    await record_audit(
        db, actor=actor, action="print.agent_key", entity_type="print_settings",
        request=request,
    )
    await db.commit()
    return {"key": key}


# ─── A helyi nyomtató-ügynök végpontjai (X-Agent-Key) ────────────────────────


async def _agent_settings(db: AsyncSession, agent_key: str | None) -> PrintSettings:
    st = await _get_or_create_settings(db)
    if not st.agent_key or not agent_key or not secrets.compare_digest(
        st.agent_key, agent_key
    ):
        raise HTTPException(status_code=401, detail={"code": "print.bad_agent_key"})
    st.agent_last_seen = datetime.now(UTC)
    return st


@agent_router.get("/jobs")
async def agent_poll(
    db: AsyncSession = Depends(get_db),
    x_agent_key: str | None = Header(default=None),
):
    """A várakozó feladatok az ügynöknek (EZPL-lel együtt)."""
    await _agent_settings(db, x_agent_key)
    jobs = (
        await db.execute(
            select(PrintJob)
            .where(PrintJob.status == "pending")
            .order_by(PrintJob.created_at)
            .limit(10)
        )
    ).scalars().all()
    out = [{"id": str(j.id), "label": j.label, "payload": j.payload} for j in jobs]
    await db.commit()
    return {"jobs": out}


class AgentAckBody(BaseModel):
    ok: bool
    error: str | None = Field(default=None, max_length=2000)


@agent_router.post("/jobs/{job_id}")
async def agent_ack(
    job_id: str,
    body: AgentAckBody,
    db: AsyncSession = Depends(get_db),
    x_agent_key: str | None = Header(default=None),
):
    """Az ügynök visszajelzése: a feladat kiment a nyomtatóra / hibára futott."""
    await _agent_settings(db, x_agent_key)
    try:
        jid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "print.job_not_found"})
    job = (
        await db.execute(select(PrintJob).where(PrintJob.id == jid))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "print.job_not_found"})
    if body.ok:
        job.status = "done"
        job.error = None
        job.printed_at = datetime.now(UTC)
    else:
        job.status = "error"
        job.error = body.error or "ismeretlen hiba"
    await db.commit()
    return {"ok": True}
