"""Üzemeltetői (Flotta-pult) végpontok: a példány licencének táv-kezelése.

Csak a WFM_OPERATOR_TOKEN birtokában hívhatók (X-Operator-Token fejléc) —
a tokent a Flotta-pult tárolja titkosítva; beállítatlan tokennél a végpontok
teljesen zárva vannak. Lejárt licencnél is működnek (a hosszabbításhoz épp
ez kell), ezért a lejárat-középréteg kivétel-listáján szerepelnek.
"""

from __future__ import annotations

import secrets as _secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.settings import LicenseBody, _license_status_payload, apply_license_update
from app.core.config import get_settings
from app.db import get_db

router = APIRouter()


async def require_operator(
    x_operator_token: str | None = Header(default=None),
) -> None:
    token = get_settings().operator_token
    if (
        not token
        or not x_operator_token
        or not _secrets.compare_digest(token, x_operator_token)
    ):
        raise HTTPException(status_code=403, detail={"code": "operator.forbidden"})


@router.get("/status", dependencies=[Depends(require_operator)])
async def operator_status(db: AsyncSession = Depends(get_db)):
    """Példány-állapot a Flotta-pultnak: licenc + kihasználtság."""
    return {"app": "iwfm", **(await _license_status_payload(db))}


# ─── Hibajelentések táv-kezelése a Flotta-pultból ───────────────────────────


@router.get("/bugs", dependencies=[Depends(require_operator)])
async def operator_bugs(
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    from app.api.bugs import _outs
    from app.models import BugReport

    q = select(BugReport).order_by(BugReport.created_at.desc()).limit(300)
    if status:
        q = q.where(BugReport.status == status)
    if severity:
        q = q.where(BugReport.severity == severity)
    rows = (await db.execute(q)).scalars().all()
    return await _outs(db, rows)


@router.post("/bugs/{bug_id}/comments", dependencies=[Depends(require_operator)])
async def operator_bug_comment(
    bug_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    body: dict | None = None,
):
    """Az üzemeltető kérdez / válaszol a jegyen (114. kör). Státusz nélkül a
    nyitott jegy needs_info-ra kerül (visszadobva a bejelentőnek), aki a
    felületen felugró ablakban kapja meg a kérdést."""
    from app.api.bugs import _bug_or_404, _outs, add_comment
    from app.api.deps import record_audit

    body = body or {}
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail={"code": "bugs.empty_comment"})
    b = await _bug_or_404(db, bug_id)
    await add_comment(
        db, b, kind="operator", author_name=(body.get("author_name") or "Üzemeltető"),
        text=text, status=body.get("status") or None,
    )
    await record_audit(
        db, actor=None, action="bug.operator_comment", entity_type="bug",
        entity_id=str(b.id), detail={"status": b.status}, request=request,
    )
    await db.commit()
    return (await _outs(db, [b]))[0]


@router.post("/bugs/{bug_id}/seen", dependencies=[Depends(require_operator)])
async def operator_bug_seen(bug_id: str, db: AsyncSession = Depends(get_db)):
    from datetime import UTC as _UTC, datetime as _dt

    from app.api.bugs import _bug_or_404

    b = await _bug_or_404(db, bug_id)
    b.operator_seen_at = _dt.now(_UTC)
    await db.commit()
    return {"ok": True}


@router.patch("/bugs/{bug_id}", dependencies=[Depends(require_operator)])
async def operator_bug_update(
    bug_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    body: dict | None = None,
):
    from datetime import UTC as _UTC, datetime as _dt

    from app.api.bugs import SEVERITIES, STATUSES, _bug_or_404, _out
    from app.api.deps import record_audit

    b = await _bug_or_404(db, bug_id)
    body = body or {}
    if body.get("status") is not None:
        if body["status"] not in STATUSES:
            raise HTTPException(status_code=422, detail={"code": "bugs.bad_status"})
        b.status = body["status"]
    if body.get("severity") is not None:
        if body["severity"] not in SEVERITIES:
            raise HTTPException(status_code=422, detail={"code": "bugs.bad_severity"})
        b.severity = body["severity"]
    if "fix_group" in body:
        b.fix_group = (body.get("fix_group") or "").strip()[:64] or None
    if "resolution_note" in body:
        b.resolution_note = (body.get("resolution_note") or "").strip()[:512] or None
    b.updated_at = _dt.now(_UTC)
    await record_audit(
        db, actor=None, action="bug.operator_update", entity_type="bug",
        entity_id=str(b.id), detail={"status": body.get("status")}, request=request,
    )
    await db.commit()
    return _out(b)


@router.delete("/bugs/{bug_id}", dependencies=[Depends(require_operator)])
async def operator_bug_delete(
    bug_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Hibajegy végleges törlése a Flotta-pultból — teszt-időszaki
    takarításhoz."""
    from app.api.bugs import _bug_or_404
    from app.api.deps import record_audit

    b = await _bug_or_404(db, bug_id)
    await record_audit(
        db, actor=None, action="bug.operator_delete", entity_type="bug",
        entity_id=str(b.id), detail={"description": b.description[:80]},
        request=request,
    )
    await db.delete(b)
    await db.commit()
    return {"ok": True}


@router.get("/bugs/{bug_id}/screenshot", dependencies=[Depends(require_operator)])
async def operator_bug_screenshot(
    bug_id: str,
    db: AsyncSession = Depends(get_db),
):
    from app.api.bugs import _bug_or_404

    b = await _bug_or_404(db, bug_id)
    if b.screenshot is None:
        raise HTTPException(status_code=404, detail={"code": "bugs.no_screenshot"})
    return Response(
        content=bytes(b.screenshot), media_type=b.screenshot_mime or "image/png"
    )


ROADMAP_STATUSES = ("planned", "in_progress", "done")


class RoadmapPushBody(BaseModel):
    # [{title, description, status, eta, sort}] — teljes csere (a Flotta a forrás)
    items: list[dict] = Field(max_length=200)


@router.put("/roadmap", dependencies=[Depends(require_operator)])
async def operator_set_roadmap(
    body: RoadmapPushBody,
    db: AsyncSession = Depends(get_db),
):
    """A Flotta-pult roadmap-lenyomása (115. kör): a példány 🗺 Roadmap
    menüje ebből épül. Teljes csere, tisztítva."""
    from app.models import LicenseSettings
    from app.services.wfm import license as license_service

    row = await license_service.get_license_row(db)
    if row is None:
        row = LicenseSettings(id=1)
        db.add(row)
    cleaned = []
    for it in body.items[:200]:
        title = str(it.get("title") or "").strip()[:256]
        if not title:
            continue
        status = str(it.get("status") or "planned")
        cleaned.append({
            "title": title,
            "description": str(it.get("description") or "").strip()[:4000],
            "status": status if status in ROADMAP_STATUSES else "planned",
            "eta": str(it.get("eta") or "").strip()[:64],
            "sort": int(it.get("sort") or 0),
        })
    cleaned.sort(key=lambda x: x["sort"])
    row.roadmap = cleaned
    await db.commit()
    license_service.invalidate_cache()
    return {"ok": True, "stored": len(cleaned)}


ROADMAP_STATUSES = ("planned", "in_progress", "done")


class RoadmapPushBody(BaseModel):
    # [{title, description, status, eta, sort}] — teljes csere (a Flotta a forrás)
    items: list[dict] = Field(max_length=200)


@router.put("/roadmap", dependencies=[Depends(require_operator)])
async def operator_set_roadmap(
    body: RoadmapPushBody,
    db: AsyncSession = Depends(get_db),
):
    """A Flotta-pult roadmap-lenyomása (115. kör): a példány 🗺 Roadmap
    menüje ebből épül. Teljes csere, tisztítva."""
    from app.models import LicenseSettings
    from app.services.wfm import license as license_service

    row = await license_service.get_license_row(db)
    if row is None:
        row = LicenseSettings(id=1)
        db.add(row)
    cleaned = []
    for it in body.items[:200]:
        title = str(it.get("title") or "").strip()[:256]
        if not title:
            continue
        status = str(it.get("status") or "planned")
        cleaned.append({
            "title": title,
            "description": str(it.get("description") or "").strip()[:4000],
            "status": status if status in ROADMAP_STATUSES else "planned",
            "eta": str(it.get("eta") or "").strip()[:64],
            "sort": int(it.get("sort") or 0),
        })
    cleaned.sort(key=lambda x: x["sort"])
    row.roadmap = cleaned
    await db.commit()
    license_service.invalidate_cache()
    return {"ok": True, "stored": len(cleaned)}


ROADMAP_STATUSES = ("planned", "in_progress", "done")


class RoadmapPushBody(BaseModel):
    # [{title, description, status, eta, sort}] — teljes csere (a Flotta a forrás)
    items: list[dict] = Field(max_length=200)


@router.put("/roadmap", dependencies=[Depends(require_operator)])
async def operator_set_roadmap(
    body: RoadmapPushBody,
    db: AsyncSession = Depends(get_db),
):
    """A Flotta-pult roadmap-lenyomása (115. kör): a példány 🗺 Roadmap
    menüje ebből épül. Teljes csere, tisztítva."""
    from app.models import LicenseSettings
    from app.services.wfm import license as license_service

    row = await license_service.get_license_row(db)
    if row is None:
        row = LicenseSettings(id=1)
        db.add(row)
    cleaned = []
    for it in body.items[:200]:
        title = str(it.get("title") or "").strip()[:256]
        if not title:
            continue
        status = str(it.get("status") or "planned")
        cleaned.append({
            "title": title,
            "description": str(it.get("description") or "").strip()[:4000],
            "status": status if status in ROADMAP_STATUSES else "planned",
            "eta": str(it.get("eta") or "").strip()[:64],
            "sort": int(it.get("sort") or 0),
        })
    cleaned.sort(key=lambda x: x["sort"])
    row.roadmap = cleaned
    await db.commit()
    license_service.invalidate_cache()
    return {"ok": True, "stored": len(cleaned)}


class PlanCatalogBody(BaseModel):
    # [{code, name, max_users, max_employees, price_monthly, price_yearly}]
    plans: list[dict] = Field(max_length=50)


@router.put("/plan-catalog", dependencies=[Depends(require_operator)])
async def operator_set_plan_catalog(
    body: PlanCatalogBody,
    db: AsyncSession = Depends(get_db),
):
    """A Flotta-pult csomag-katalógusának lenyomása — az ügyfél-oldali
    "Elérhető csomagok" kártyák ebből épülnek (név + limit + ár)."""
    from app.models import LicenseSettings
    from app.services.wfm import license as license_service

    row = await license_service.get_license_row(db)
    if row is None:
        row = LicenseSettings(id=1)
        db.add(row)
    cleaned = []
    for p in body.plans[:50]:
        code = str(p.get("code") or "").strip().lower()[:16]
        if not code:
            continue
        cleaned.append({
            "code": code,
            "name": str(p.get("name") or code.upper())[:64],
            "max_users": p.get("max_users"),
            "max_employees": p.get("max_employees"),
            "price_monthly": p.get("price_monthly"),
            "price_yearly": p.get("price_yearly"),
        })
    row.plan_catalog = cleaned
    await db.commit()
    license_service.invalidate_cache()
    return {"ok": True, "count": len(cleaned)}


@router.put("/license", dependencies=[Depends(require_operator)])
async def operator_set_license(
    body: LicenseBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Licenc-sáv / érvényesség állítása a Flotta-pultból (actor nélkül,
    de audit-naplóval)."""
    return await apply_license_update(db, body, actor=None, request=request)
