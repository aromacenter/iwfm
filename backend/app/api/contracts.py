"""Partner-szerződések kezelése — a partnertől külön entitás, érvényességgel.

Egy partnernek több szerződése lehet (történet + jövőbeli); az elszámolás a
mindenkor aktívat alkalmazza (services/wfm/contracts.apply_active_contract).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_perm
from app.db import get_db
from app.models import Partner, PartnerContract, User
from app.services.wfm.contracts import apply_active_contract

router = APIRouter()


async def _get_partner_or_404(db: AsyncSession, partner_id: str) -> Partner:
    try:
        pid = uuid.UUID(partner_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    p = (await db.execute(select(Partner).where(Partner.id == pid))).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail={"code": "partner.not_found"})
    return p


async def _get_contract_or_404(
    db: AsyncSession, partner: Partner, contract_id: str
) -> PartnerContract:
    try:
        cid = uuid.UUID(contract_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "contract.not_found"})
    c = (
        await db.execute(
            select(PartnerContract).where(
                PartnerContract.id == cid, PartnerContract.partner_id == partner.id
            )
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail={"code": "contract.not_found"})
    return c


class ContractBody(BaseModel):
    valid_from: date
    valid_to: date | None = None
    min_portions: int | None = Field(default=None, ge=0, le=1_000_000)
    below_min_price: float | None = Field(default=None, ge=0)  # Ft/adag
    min_kg: float | None = Field(default=None, ge=0, le=100_000)
    below_min_price_kg: float | None = Field(default=None, ge=0)  # Ft/kg
    rent_if_below_min: bool = False
    note: str | None = None


class ContractOut(BaseModel):
    id: str
    valid_from: date
    valid_to: date | None
    min_portions: int | None
    below_min_price: float | None
    min_kg: float | None
    below_min_price_kg: float | None
    rent_if_below_min: bool
    note: str | None
    status: str  # active | future | expired
    created_at: datetime


def _status(c: PartnerContract, today: date) -> str:
    if c.valid_from > today:
        return "future"
    if c.valid_to is not None and c.valid_to < today:
        return "expired"
    return "active"


def _out(c: PartnerContract) -> ContractOut:
    return ContractOut(
        id=str(c.id), valid_from=c.valid_from, valid_to=c.valid_to,
        min_portions=c.min_portions, below_min_price=c.below_min_price,
        min_kg=c.min_kg, below_min_price_kg=c.below_min_price_kg,
        rent_if_below_min=c.rent_if_below_min, note=c.note,
        status=_status(c, date.today()), created_at=c.created_at,
    )


def _check_dates(body: ContractBody) -> None:
    if body.valid_to is not None and body.valid_to < body.valid_from:
        raise HTTPException(status_code=422, detail={"code": "contract.bad_dates"})


@router.get("/{partner_id}/contracts", response_model=list[ContractOut])
async def list_contracts(
    partner_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_perm("partners")),
):
    partner = await _get_partner_or_404(db, partner_id)
    rows = (
        await db.execute(
            select(PartnerContract)
            .where(PartnerContract.partner_id == partner.id)
            .order_by(PartnerContract.valid_from.desc())
        )
    ).scalars().all()
    return [_out(c) for c in rows]


@router.post("/{partner_id}/contracts", response_model=ContractOut, status_code=201)
async def create_contract(
    partner_id: str,
    body: ContractBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    partner = await _get_partner_or_404(db, partner_id)
    _check_dates(body)
    c = PartnerContract(partner_id=partner.id, created_by=actor.id, **body.model_dump())
    db.add(c)
    await db.flush()
    await apply_active_contract(db, partner)
    await record_audit(
        db, actor=actor, action="contract.create", entity_type="partner_contract",
        entity_id=str(c.id), detail={"partner": partner.name, "from": str(body.valid_from)},
        request=request,
    )
    await db.commit()
    return _out(c)


@router.patch("/{partner_id}/contracts/{contract_id}", response_model=ContractOut)
async def update_contract(
    partner_id: str,
    contract_id: str,
    body: ContractBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    partner = await _get_partner_or_404(db, partner_id)
    c = await _get_contract_or_404(db, partner, contract_id)
    _check_dates(body)
    for key, value in body.model_dump().items():
        setattr(c, key, value)
    await apply_active_contract(db, partner)
    await record_audit(
        db, actor=actor, action="contract.update", entity_type="partner_contract",
        entity_id=str(c.id), detail={"partner": partner.name}, request=request,
    )
    await db.commit()
    return _out(c)


@router.delete("/{partner_id}/contracts/{contract_id}")
async def delete_contract(
    partner_id: str,
    contract_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_perm("partners")),
):
    partner = await _get_partner_or_404(db, partner_id)
    c = await _get_contract_or_404(db, partner, contract_id)
    await record_audit(
        db, actor=actor, action="contract.delete", entity_type="partner_contract",
        entity_id=str(c.id),
        detail={"partner": partner.name, "from": str(c.valid_from)},
        request=request,
    )
    await db.delete(c)
    await db.flush()
    await apply_active_contract(db, partner)
    await db.commit()
    return {"ok": True}
