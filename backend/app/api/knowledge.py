"""Tudásbázis-bejegyzések: kézzel rögzített, bevált megoldások gépenként.

A szerviz-joggal rendelkezők (szervizes, manager, admin) rögzíthetik a
gyakorlatban bevált, kézikönyvekben nem szereplő megoldásokat. A QR-oldali
AI chat a Beállításokban tárolt tudásbázis-szöveg MELLETT ezekből is
válaszol, a gép típusára szűrve. Törlés a törlés-joghoz kötött.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_any_perm, require_perm
from app.db import get_db
from app.models import Asset, KnowledgeEntry, User

router = APIRouter()


class EntryBody(BaseModel):
    machine_label: str = Field(min_length=2, max_length=256)
    title: str = Field(min_length=2, max_length=256)
    content: str = Field(min_length=5, max_length=8000)


class EntryOut(BaseModel):
    id: str
    machine_label: str
    title: str
    content: str
    source: str
    created_by_name: str | None
    created_at: datetime
    updated_at: datetime


def _out(e: KnowledgeEntry) -> EntryOut:
    return EntryOut(
        id=str(e.id),
        machine_label=e.machine_label,
        title=e.title,
        content=e.content,
        source=e.source,
        created_by_name=e.created_by_name,
        created_at=e.created_at,
        updated_at=e.updated_at,
    )


async def _get_or_404(db: AsyncSession, entry_id: str) -> KnowledgeEntry:
    try:
        eid = uuid.UUID(entry_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "kb.not_found"})
    e = (
        await db.execute(select(KnowledgeEntry).where(KnowledgeEntry.id == eid))
    ).scalar_one_or_none()
    if e is None:
        raise HTTPException(status_code=404, detail={"code": "kb.not_found"})
    return e


@router.get("", response_model=list[EntryOut])
async def list_entries(
    q: str | None = Query(default=None),
    machine: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_any_perm("service", "knowledge")),
):
    query = select(KnowledgeEntry).order_by(KnowledgeEntry.updated_at.desc())
    if machine:
        query = query.where(KnowledgeEntry.machine_label.ilike(f"%{machine}%"))
    if q:
        like = f"%{q}%"
        query = query.where(
            KnowledgeEntry.title.ilike(like)
            | KnowledgeEntry.content.ilike(like)
            | KnowledgeEntry.machine_label.ilike(like)
        )
    rows = (await db.execute(query.limit(500))).scalars().all()
    return [_out(e) for e in rows]


@router.get("/machines")
async def list_machines(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_any_perm("service", "knowledge")),
):
    """Géptípus-javaslatok a legördülőhöz: a géptörzs gyártó+típus kombinációi
    és a már használt bejegyzés-címkék."""
    from app.services.wfm.kb_auto import machine_label

    assets = (
        await db.execute(select(Asset.manufacturer, Asset.name).distinct())
    ).all()
    labels = {machine_label(m, n) for m, n in assets if machine_label(m, n)}
    used = (
        await db.execute(select(KnowledgeEntry.machine_label).distinct())
    ).scalars().all()
    labels.update(used)
    return sorted(label for label in labels if label)


@router.post("", response_model=EntryOut, status_code=201)
async def create_entry(
    body: EntryBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_any_perm("service", "knowledge")),
):
    e = KnowledgeEntry(
        machine_label=body.machine_label.strip(),
        title=body.title.strip(),
        content=body.content.strip(),
        source="manual",
        created_by=actor.id,
        created_by_name=actor.display_name,
    )
    db.add(e)
    await db.flush()
    await record_audit(
        db, actor=actor, action="kb.create", entity_type="knowledge_entry",
        entity_id=str(e.id), detail={"machine": e.machine_label, "title": e.title},
        request=request,
    )
    await db.commit()
    return _out(e)


@router.patch("/{entry_id}", response_model=EntryOut)
async def update_entry(
    entry_id: str,
    body: EntryBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_any_perm("service", "knowledge")),
):
    e = await _get_or_404(db, entry_id)
    e.machine_label = body.machine_label.strip()
    e.title = body.title.strip()
    e.content = body.content.strip()
    await record_audit(
        db, actor=actor, action="kb.update", entity_type="knowledge_entry",
        entity_id=str(e.id), request=request,
    )
    await db.commit()
    await db.refresh(e)  # a szerver-oldali updated_at (onupdate) frissül
    return _out(e)


class BulkDeleteBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


@router.post("/bulk-delete")
async def bulk_delete_entries(
    body: BulkDeleteBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("delete")),
):
    deleted = 0
    for raw in body.ids:
        try:
            eid = uuid.UUID(raw)
        except ValueError:
            continue
        e = (
            await db.execute(select(KnowledgeEntry).where(KnowledgeEntry.id == eid))
        ).scalar_one_or_none()
        if e is None:
            continue
        await db.delete(e)
        deleted += 1
    await record_audit(
        db, actor=actor, action="kb.bulk_delete", entity_type="knowledge_entry",
        detail={"deleted": deleted}, request=request,
    )
    await db.commit()
    return {"deleted": deleted, "blocked": []}
