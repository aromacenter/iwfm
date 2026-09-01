"""Szerviz: hibajegyek + karbantartás a gépekhez.

A jegyek géphez (és a gép partneréhez) köthetők, de általános jegy is
felvehető. A karbantartás-esedékesség a gép számlálója és normája alapján
számolódik: az utolsó kész karbantartás számláló-állásától (vagy 0-tól) a
norma elérésekor esedékes az újabb karbantartás.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, record_audit, require_perm
from app.db import get_db
from app.models import Asset, Partner, ServiceTicket, TicketAttachment, User

router = APIRouter()

KINDS = ("repair", "maintenance")
STATUSES = ("open", "in_progress", "done", "cancelled")
PRIORITIES = ("low", "normal", "high")


async def _next_ticket_no(db: AsyncSession) -> str:
    """SZ-0001 formátumú, növekvő jegyszám."""
    last = (
        await db.execute(
            select(ServiceTicket.ticket_no)
            .where(ServiceTicket.ticket_no.like("SZ-%"))
            .order_by(ServiceTicket.ticket_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    number = 0
    if last:
        try:
            number = int(last.split("-", 1)[1])
        except (IndexError, ValueError):
            number = 0
    return f"SZ-{number + 1:04d}"


class TicketBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    kind: str = "repair"
    priority: str = "normal"
    description: str | None = Field(default=None, max_length=4000)
    asset_id: str | None = None
    assigned_to_user_id: str | None = None


class TicketPart(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    qty: float = Field(default=1, ge=0, le=1000)
    unit_price: float = Field(default=0, ge=0)  # nettó Ft/db


class TicketUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    kind: str | None = None
    priority: str | None = None
    status: str | None = None
    description: str | None = Field(default=None, max_length=4000)
    assigned_to_user_id: str | None = None
    resolution: str | None = Field(default=None, max_length=4000)
    counter_at_service: int | None = Field(default=None, ge=0)
    parts: list[TicketPart] | None = Field(default=None, max_length=30)
    labor_fee: float | None = Field(default=None, ge=0)


class TicketOut(BaseModel):
    id: str
    ticket_no: str
    kind: str
    status: str
    priority: str
    title: str
    description: str | None
    asset_id: str | None
    asset_label: str | None
    partner_id: str | None
    partner_label: str | None
    assigned_to_user_id: str | None
    assigned_to_name: str | None
    counter_at_service: int | None
    resolution: str | None
    parts: list[dict] | None = None
    labor_fee: float | None = None
    total_cost: float = 0.0  # alkatrészek + munkadíj (nettó)
    resolved_at: datetime | None
    created_at: datetime


def _out(tk: ServiceTicket) -> TicketOut:
    return TicketOut(
        id=str(tk.id),
        ticket_no=tk.ticket_no,
        kind=tk.kind,
        status=tk.status,
        priority=tk.priority,
        title=tk.title,
        description=tk.description,
        asset_id=str(tk.asset_id) if tk.asset_id else None,
        asset_label=tk.asset_label,
        partner_id=str(tk.partner_id) if tk.partner_id else None,
        partner_label=tk.partner_label,
        assigned_to_user_id=str(tk.assigned_to_user_id) if tk.assigned_to_user_id else None,
        assigned_to_name=tk.assigned_to_name,
        counter_at_service=tk.counter_at_service,
        resolution=tk.resolution,
        parts=tk.parts,
        labor_fee=tk.labor_fee,
        total_cost=round(
            (tk.labor_fee or 0)
            + sum(
                float(p.get("qty", 0)) * float(p.get("unit_price", 0))
                for p in (tk.parts or [])
                if isinstance(p, dict)
            ),
            2,
        ),
        resolved_at=tk.resolved_at,
        created_at=tk.created_at,
    )


async def _get_ticket_or_404(db: AsyncSession, ticket_id: str) -> ServiceTicket:
    try:
        tid = uuid.UUID(ticket_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "service.not_found"})
    tk = (
        await db.execute(select(ServiceTicket).where(ServiceTicket.id == tid))
    ).scalar_one_or_none()
    if tk is None:
        raise HTTPException(status_code=404, detail={"code": "service.not_found"})
    return tk


async def _resolve_assignee(db: AsyncSession, raw: str | None) -> tuple[uuid.UUID | None, str | None]:
    if not raw:
        return None, None
    try:
        uid = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "service.bad_assignee"})
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=422, detail={"code": "service.bad_assignee"})
    return user.id, user.display_name


async def _resolve_asset(
    db: AsyncSession, raw: str | None
) -> tuple[Asset | None, str | None, uuid.UUID | None, str | None]:
    """(asset, asset_label, partner_id, partner_label) — a partner a gép
    kihelyezési helye, ha van."""
    if not raw:
        return None, None, None, None
    try:
        aid = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "asset.not_found"})
    asset = (await db.execute(select(Asset).where(Asset.id == aid))).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail={"code": "asset.not_found"})
    label = f"{asset.name} ({asset.barcode})"
    partner_id = asset.partner_id
    partner_label = None
    if partner_id is not None:
        partner_label = (
            await db.execute(select(Partner.name).where(Partner.id == partner_id))
        ).scalar_one_or_none()
    return asset, label, partner_id, partner_label


@router.get("", response_model=list[TicketOut])
async def list_tickets(
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    assigned_to: str | None = Query(default=None),
    asset_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    # Szerviz-jog nélkül (pl. alvállalkozó szervizes) KIZÁRÓLAG a saját, rá
    # kiosztott jegyeit kérdezheti le — mások jegyeit soha.
    from app.api.deps import get_permission_matrix, permissions_for

    perms = permissions_for(actor.role, await get_permission_matrix(db))
    if "service" not in perms and assigned_to != str(actor.id):
        raise HTTPException(status_code=403, detail={"code": "auth.forbidden"})
    query = select(ServiceTicket).order_by(ServiceTicket.created_at.desc())
    if status:
        if status not in STATUSES:
            raise HTTPException(status_code=422, detail={"code": "service.bad_status"})
        query = query.where(ServiceTicket.status == status)
    if kind:
        if kind not in KINDS:
            raise HTTPException(status_code=422, detail={"code": "service.bad_kind"})
        query = query.where(ServiceTicket.kind == kind)
    if assigned_to:
        try:
            query = query.where(ServiceTicket.assigned_to_user_id == uuid.UUID(assigned_to))
        except ValueError:
            raise HTTPException(status_code=422, detail={"code": "service.bad_assignee"})
    if asset_id:
        try:
            query = query.where(ServiceTicket.asset_id == uuid.UUID(asset_id))
        except ValueError:
            raise HTTPException(status_code=404, detail={"code": "asset.not_found"})
    if q:
        like = f"%{q}%"
        query = query.where(
            ServiceTicket.title.ilike(like)
            | ServiceTicket.ticket_no.ilike(like)
            | ServiceTicket.asset_label.ilike(like)
            | ServiceTicket.partner_label.ilike(like)
        )
    rows = (await db.execute(query.limit(1000))).scalars().all()
    return [_out(tk) for tk in rows]


@router.get("/assignees")
async def list_assignees(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("service")),
):
    """Kiosztható felhasználók (szervizes, manager, admin) a legördülőhöz."""
    rows = (
        await db.execute(
            select(User)
            .where(User.is_active.is_(True), User.role.in_(("szervizes", "manager", "admin")))
            .order_by(User.display_name)
        )
    ).scalars().all()
    return [{"id": str(u.id), "name": u.display_name, "role": u.role} for u in rows]


@router.post("/maintenance-baseline")
async def maintenance_baseline(
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("service")),
):
    """Karbantartás-alapvonal: minden számlálós gépre, amelynek még nincs KÉSZ
    karbantartás-jegye, létrehoz egy lezárt „alapvonal” jegyet a jelenlegi
    számláló-állással. Így az importált gépek esedékesség-listája kinullázódik,
    és az igazi esedékesség a mostani állástól számítódik. Idempotens: akinek
    már van kész karbantartása, azt nem bántja."""
    assets = (
        await db.execute(
            select(Asset).where(Asset.counter.is_not(None), Asset.status != "retired")
        )
    ).scalars().all()
    have_done = {
        aid
        for (aid,) in (
            await db.execute(
                select(ServiceTicket.asset_id.distinct()).where(
                    ServiceTicket.kind == "maintenance",
                    ServiceTicket.status == "done",
                    ServiceTicket.asset_id.is_not(None),
                )
            )
        ).all()
    }
    targets = [a for a in assets if a.id not in have_done]
    if not targets:
        return {"created": 0}

    last = (
        await db.execute(
            select(ServiceTicket.ticket_no)
            .where(ServiceTicket.ticket_no.like("SZ-%"))
            .order_by(ServiceTicket.ticket_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    number = 0
    if last:
        try:
            number = int(last.split("-", 1)[1])
        except (IndexError, ValueError):
            number = 0

    now = datetime.now(UTC)
    for a in targets:
        number += 1
        db.add(
            ServiceTicket(
                ticket_no=f"SZ-{number:04d}",
                kind="maintenance",
                status="done",
                priority="low",
                title="Karbantartás-alapvonal (jelenlegi állás rögzítése)",
                description="Automatikus alapvonal: az esedékesség innentől számítódik.",
                asset_id=a.id,
                asset_label=f"{a.name} ({a.barcode})",
                partner_id=a.partner_id,
                counter_at_service=a.counter,
                resolved_at=now,
                created_by=actor.id,
            )
        )
    await record_audit(
        db, actor=actor, action="service.baseline", entity_type="service_ticket",
        detail={"created": len(targets)}, request=request,
    )
    await db.commit()
    return {"created": len(targets)}


@router.get("/maintenance-due")
async def maintenance_due(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("service")),
):
    """Karbantartásra esedékes gépek: ahol a számláló az utolsó kész
    karbantartás óta elérte a normát. Nyitott karbantartás-jeggyel rendelkező
    gépet nem jelzünk újra."""
    assets = (
        await db.execute(
            select(Asset).where(
                Asset.norm.is_not(None), Asset.norm > 0, Asset.counter.is_not(None),
                Asset.status != "retired",
            )
        )
    ).scalars().all()
    if not assets:
        return []

    asset_ids = [a.id for a in assets]
    # utolsó kész karbantartás számláló-állása gépenként
    done_rows = (
        await db.execute(
            select(ServiceTicket.asset_id, ServiceTicket.counter_at_service)
            .where(
                ServiceTicket.asset_id.in_(asset_ids),
                ServiceTicket.kind == "maintenance",
                ServiceTicket.status == "done",
            )
            .order_by(ServiceTicket.asset_id, ServiceTicket.resolved_at.desc())
        )
    ).all()
    last_service: dict[uuid.UUID, int] = {}
    for aid, counter in done_rows:
        if aid not in last_service and counter is not None:
            last_service[aid] = counter
    open_maint = {
        aid
        for (aid,) in (
            await db.execute(
                select(ServiceTicket.asset_id).where(
                    ServiceTicket.asset_id.in_(asset_ids),
                    ServiceTicket.kind == "maintenance",
                    ServiceTicket.status.in_(("open", "in_progress")),
                )
            )
        ).all()
    }

    partner_names = {
        pid: name
        for pid, name in (
            await db.execute(
                select(Partner.id, Partner.name).where(
                    Partner.id.in_([a.partner_id for a in assets if a.partner_id])
                )
            )
        ).all()
    } if any(a.partner_id for a in assets) else {}

    due = []
    for a in assets:
        base = last_service.get(a.id, 0)
        used = (a.counter or 0) - base
        if used < (a.norm or 0) or a.id in open_maint:
            continue
        due.append(
            {
                "asset_id": str(a.id),
                "barcode": a.barcode,
                "name": a.name,
                "partner_name": partner_names.get(a.partner_id),
                "counter": a.counter,
                "norm": a.norm,
                "since_last_service": used,
                "overdue_by": used - (a.norm or 0),
            }
        )
    due.sort(key=lambda d: -d["overdue_by"])
    return due


@router.post("", response_model=TicketOut, status_code=201)
async def create_ticket(
    body: TicketBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("service")),
):
    if body.kind not in KINDS:
        raise HTTPException(status_code=422, detail={"code": "service.bad_kind"})
    if body.priority not in PRIORITIES:
        raise HTTPException(status_code=422, detail={"code": "service.bad_priority"})
    asset, asset_label, partner_id, partner_label = await _resolve_asset(db, body.asset_id)
    assignee_id, assignee_name = await _resolve_assignee(db, body.assigned_to_user_id)

    tk = ServiceTicket(
        ticket_no=await _next_ticket_no(db),
        kind=body.kind,
        priority=body.priority,
        title=body.title,
        description=body.description,
        asset_id=asset.id if asset else None,
        asset_label=asset_label,
        partner_id=partner_id,
        partner_label=partner_label,
        assigned_to_user_id=assignee_id,
        assigned_to_name=assignee_name,
        created_by=actor.id,
    )
    db.add(tk)
    await db.flush()
    await _sync_assignee_task(db, tk, actor)
    await record_audit(
        db, actor=actor, action="service.create", entity_type="service_ticket",
        entity_id=tk.ticket_no, detail={"title": body.title}, request=request,
    )
    await db.commit()

    from app.services.wfm.automation import fire_event

    fire_event("ticket.created", {
        "_partner_id": partner_id,
        "cim": tk.title,
        "prioritas": tk.priority,
        "fajta": tk.kind,
        "gep_vonalkod": asset.barcode if asset else "",
        "gep_nev": asset_label or "",
        "partner_nev": partner_label or "",
        "bejelento": actor.display_name,
    })
    await _notify_ticket_assignee(db, tk)
    return _out(tk)


async def _sync_assignee_task(db: AsyncSession, tk: ServiceTicket, actor: User) -> None:
    """A felelősnek dedikált feladat (+munkalap) a jegyből: a kijelölt kolléga
    a Feladataim nézetben látja a jegy minden adatát és képét — akkor is, ha
    a Szerviz menühöz nincs jogosultsága. Átosztásnál a feladat is átszáll."""
    if tk.assigned_to_user_id is None:
        return
    from datetime import date as _date

    from app.models import Employee, Task, Worksheet

    emp = (
        await db.execute(
            select(Employee).where(Employee.user_id == tk.assigned_to_user_id)
        )
    ).scalar_one_or_none()
    if emp is None:
        return  # nincs dolgozói törzsadata (pl. tiszta admin-fiók)
    existing = (
        await db.execute(select(Task).where(Task.service_ticket_id == tk.id))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.employee_id != emp.id:
            existing.employee_id = emp.id
            if existing.status == "done":
                existing.status = "open"
        return
    parts = [tk.description or ""]
    extra = []
    if tk.asset_label:
        extra.append(f"Gép: {tk.asset_label}")
    if tk.partner_label:
        extra.append(f"Partner: {tk.partner_label}")
    if tk.priority == "high":
        extra.append("⚠️ SÜRGŐS")
    if extra:
        parts.append("\n".join(extra))
    task = Task(
        title=f"🔧 {tk.ticket_no} — {tk.title}"[:256],
        description="\n\n".join(p for p in parts if p).strip() or None,
        employee_id=emp.id,
        due_date=_date.today(),
        status="open",
        created_by=actor.id,
        service_ticket_id=tk.id,
    )
    db.add(task)
    await db.flush()
    # a munkalap is azonnal kiáll a jegy gépével — a kolléga csak kitölti
    assignee = (
        await db.execute(select(User).where(User.id == tk.assigned_to_user_id))
    ).scalar_one_or_none()
    external = bool(assignee and assignee.role == "szervizes")
    from app.api.tasks import _next_worksheet_serial

    db.add(Worksheet(
        task_id=task.id,
        serial=await _next_worksheet_serial(db, external=external),
        external_service=external,
        asset_id=tk.asset_id,
        work_description="",
    ))


async def _notify_ticket_assignee(db: AsyncSession, tk: ServiceTicket) -> None:
    """Privát Telegram a kijelölt szervizesnek az új/átosztott jegyről — ha
    összekapcsolta magát a bottal. Best-effort."""
    if tk.assigned_to_user_id is None:
        return
    try:
        from app.models import Employee
        from app.services.wfm.telegram import send_personal

        emp = (
            await db.execute(
                select(Employee).where(Employee.user_id == tk.assigned_to_user_id)
            )
        ).scalar_one_or_none()
        if emp is None:
            return
        parts = [f"🔧 Szervizjegy neked: {tk.ticket_no} — {tk.title}"]
        if tk.asset_label:
            parts.append(tk.asset_label)
        if tk.partner_label:
            parts.append(tk.partner_label)
        if tk.priority == "high":
            parts.append("⚠️ SÜRGŐS")
        await send_personal(db, emp, " · ".join(parts))
    except Exception:
        import logging

        logging.getLogger(__name__).warning("ticket assign notify failed", exc_info=True)


@router.patch("/{ticket_id}", response_model=TicketOut)
async def update_ticket(
    ticket_id: str,
    body: TicketUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("service")),
):
    tk = await _get_ticket_or_404(db, ticket_id)
    if body.kind is not None:
        if body.kind not in KINDS:
            raise HTTPException(status_code=422, detail={"code": "service.bad_kind"})
        tk.kind = body.kind
    if body.priority is not None:
        if body.priority not in PRIORITIES:
            raise HTTPException(status_code=422, detail={"code": "service.bad_priority"})
        tk.priority = body.priority
    if body.title is not None:
        tk.title = body.title
    if body.description is not None:
        tk.description = body.description
    if body.resolution is not None:
        tk.resolution = body.resolution
    if body.assigned_to_user_id is not None:
        assignee_id, assignee_name = await _resolve_assignee(db, body.assigned_to_user_id or None)
        reassigned = assignee_id is not None and assignee_id != tk.assigned_to_user_id
        tk.assigned_to_user_id = assignee_id
        tk.assigned_to_name = assignee_name
    else:
        reassigned = False
    if body.parts is not None:
        tk.parts = [p.model_dump() for p in body.parts]
    if body.labor_fee is not None:
        tk.labor_fee = body.labor_fee
    if body.counter_at_service is not None:
        tk.counter_at_service = body.counter_at_service
        # a gép számlálóját is frissítjük, ha nagyobb a rögzített állás
        if tk.asset_id is not None:
            asset = (
                await db.execute(select(Asset).where(Asset.id == tk.asset_id))
            ).scalar_one_or_none()
            if asset is not None and (asset.counter or 0) < body.counter_at_service:
                asset.counter = body.counter_at_service
    became_done = False
    if body.status is not None:
        if body.status not in STATUSES:
            raise HTTPException(status_code=422, detail={"code": "service.bad_status"})
        became_done = body.status == "done" and tk.status != "done"
        tk.status = body.status
        tk.resolved_at = datetime.now(UTC) if body.status in ("done", "cancelled") else None

    await _sync_assignee_task(db, tk, actor)
    await record_audit(
        db, actor=actor, action="service.update", entity_type="service_ticket",
        entity_id=tk.ticket_no, request=request,
    )
    await db.commit()

    if became_done:
        from app.services.wfm.automation import fire_event

        parts_cost = sum(
            (p.get("qty") or 0) * (p.get("unit_price") or 0) for p in (tk.parts or [])
        )
        fire_event("ticket.done", {
            "_partner_id": tk.partner_id,
            "cim": tk.title,
            "gep_vonalkod": "",
            "gep_nev": tk.asset_label or "",
            "partner_nev": tk.partner_label or "",
            "koltseg_osszesen": round(parts_cost + (tk.labor_fee or 0)),
        })
    if reassigned:
        await _notify_ticket_assignee(db, tk)
    return _out(tk)


@router.get("/attachments/{attachment_id}")
async def get_attachment(
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("service")),
):
    """Csatolt kép letöltése/megjelenítése."""
    try:
        aid = uuid.UUID(attachment_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "service.not_found"})
    att = (
        await db.execute(select(TicketAttachment).where(TicketAttachment.id == aid))
    ).scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=404, detail={"code": "service.not_found"})
    return Response(
        content=bytes(att.data),
        media_type=att.mime,
        headers={"Content-Disposition": f'inline; filename="{att.filename}"'},
    )


@router.get("/{ticket_id}/attachments")
async def list_attachments(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("service")),
):
    """A jegyhez csatolt képek metaadatai (a kép a /attachments/{id} úton jön)."""
    tk = await _get_ticket_or_404(db, ticket_id)
    rows = (
        await db.execute(
            select(TicketAttachment)
            .where(TicketAttachment.ticket_id == tk.id)
            .order_by(TicketAttachment.created_at)
        )
    ).scalars().all()
    return [
        {"id": str(a.id), "filename": a.filename, "mime": a.mime, "size": len(a.data)}
        for a in rows
    ]


class BulkDeleteBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


@router.post("/bulk-delete")
async def bulk_delete_tickets(
    body: BulkDeleteBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("delete")),
):
    deleted = 0
    for raw in body.ids:
        try:
            tid = uuid.UUID(raw)
        except ValueError:
            continue
        tk = (
            await db.execute(select(ServiceTicket).where(ServiceTicket.id == tid))
        ).scalar_one_or_none()
        if tk is None:
            continue
        await db.execute(sa_delete(TicketAttachment).where(TicketAttachment.ticket_id == tid))
        await db.delete(tk)
        deleted += 1
    await record_audit(
        db, actor=actor, action="service.bulk_delete", entity_type="service_ticket",
        detail={"deleted": deleted}, request=request,
    )
    await db.commit()
    return {"deleted": deleted, "blocked": []}
