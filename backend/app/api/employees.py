"""Employee master data (Dolgozói törzsadat).

Sensitive identifiers are validated (HU checksums), stored encrypted, and
exposed only as masked values. ``GET /{id}/reveal`` returns the decrypted
values for admins and writes an audit row (GDPR accountability).

Creating an employee also creates their login account (role=employee) with a
temporary password the admin hands over; the employee should change it later.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_audit, require_role
from app.core.crypto import decrypt_pii, encrypt_pii, mask_tail
from app.core.security import hash_password
from app.db import get_db
from app.models import Employee, EmployeeSkill, Skill, User
from app.services.wfm.codes import generate_unique_employee_code
from app.services.wfm.validators import (
    is_valid_bank_account,
    is_valid_tax_id,
    is_valid_taj,
    normalize_identifier,
)

router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────────────────


class EmployeeBase(BaseModel):
    last_name: str = Field(min_length=1, max_length=128)
    first_name: str = Field(min_length=1, max_length=128)
    birth_name: str | None = Field(default=None, max_length=256)
    mother_name: str | None = Field(default=None, max_length=256)
    birth_place: str | None = Field(default=None, max_length=128)
    birth_date: date | None = None
    citizenship: str = Field(default="magyar", max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=512)
    residence_address: str | None = Field(default=None, max_length=512)
    hire_date: date
    termination_date: date | None = None
    job_title: str | None = Field(default=None, max_length=128)
    feor_code: str | None = Field(default=None, max_length=8)
    employment_type: str = "full_time"
    contract_type: str = "indefinite"
    weekly_hours: int = Field(default=40, ge=1, le=60)
    wage_type: str = "monthly"
    annual_leave_days: int = Field(default=20, ge=0, le=60)
    notes: str | None = None

    @field_validator("employment_type")
    @classmethod
    def _emp_type(cls, v: str) -> str:
        if v not in ("full_time", "part_time"):
            raise ValueError("employment_type must be full_time|part_time")
        return v

    @field_validator("contract_type")
    @classmethod
    def _contract(cls, v: str) -> str:
        if v not in ("indefinite", "fixed_term"):
            raise ValueError("contract_type must be indefinite|fixed_term")
        return v

    @field_validator("wage_type")
    @classmethod
    def _wage(cls, v: str) -> str:
        if v not in ("monthly", "hourly"):
            raise ValueError("wage_type must be monthly|hourly")
        return v


class SensitiveFields(BaseModel):
    """Plaintext identifiers — accepted on input, never echoed back."""

    tax_id: str | None = None  # adóazonosító jel
    taj: str | None = None  # TAJ szám
    bank_account: str | None = None  # bankszámlaszám
    wage_amount: str | None = None  # bér (összeg, szabad formátum pl. "650000 HUF/hó")


class EmployeeCreate(EmployeeBase, SensitiveFields):
    email: EmailStr  # login fiók email
    initial_password: str | None = Field(default=None, min_length=10, max_length=128)
    skill_ids: list[int] = Field(default_factory=list)


class EmployeeUpdate(BaseModel):
    # Minden mező opcionális — csak a megadottak módosulnak.
    model_config = {"extra": "forbid"}
    last_name: str | None = Field(default=None, min_length=1, max_length=128)
    first_name: str | None = Field(default=None, min_length=1, max_length=128)
    birth_name: str | None = None
    mother_name: str | None = None
    birth_place: str | None = None
    birth_date: date | None = None
    citizenship: str | None = None
    phone: str | None = None
    address: str | None = None
    residence_address: str | None = None
    hire_date: date | None = None
    termination_date: date | None = None
    job_title: str | None = None
    feor_code: str | None = None
    employment_type: str | None = None
    contract_type: str | None = None
    weekly_hours: int | None = Field(default=None, ge=1, le=60)
    wage_type: str | None = None
    annual_leave_days: int | None = Field(default=None, ge=0, le=60)
    notes: str | None = None
    status: str | None = None
    tax_id: str | None = None
    taj: str | None = None
    bank_account: str | None = None
    wage_amount: str | None = None
    skill_ids: list[int] | None = None  # ha megadod, lecseréli a teljes listát


class EmployeeOut(EmployeeBase):
    id: str
    user_id: str
    email: str | None = None
    status: str
    tax_id_masked: str | None = None
    taj_masked: str | None = None
    bank_account_masked: str | None = None
    has_wage: bool = False
    employee_code: str | None = None  # 6 jegyű törzsszám (blokkoló-terminál)
    skills: list[dict] = Field(default_factory=list)  # [{id, name}]
    # Csak létrehozáskor, és csak ha a jelszót a rendszer generálta — az admin
    # ekkor látja egyetlen egyszer, hogy átadhassa a dolgozónak.
    generated_password: str | None = None


class RevealOut(BaseModel):
    tax_id: str | None
    taj: str | None
    bank_account: str | None
    wage_amount: str | None


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _validate_sensitive(body: SensitiveFields) -> dict[str, str | None]:
    """Checksum-validate + normalize the HU identifiers; raise 422 on failure."""
    errors: dict[str, str] = {}
    out: dict[str, str | None] = {}
    if body.tax_id is not None:
        norm = normalize_identifier(body.tax_id)
        if norm and not is_valid_tax_id(norm):
            errors["tax_id"] = "Érvénytelen adóazonosító jel (ellenőrzőösszeg-hiba)."
        out["tax_id"] = norm or None
    if body.taj is not None:
        norm = normalize_identifier(body.taj)
        if norm and not is_valid_taj(norm):
            errors["taj"] = "Érvénytelen TAJ szám (ellenőrzőösszeg-hiba)."
        out["taj"] = norm or None
    if body.bank_account is not None:
        norm = normalize_identifier(body.bank_account)
        if norm and not is_valid_bank_account(norm):
            errors["bank_account"] = "Érvénytelen bankszámlaszám (GIRO ellenőrzés)."
        out["bank_account"] = norm or None
    if body.wage_amount is not None:
        out["wage_amount"] = body.wage_amount or None
    if errors:
        raise HTTPException(status_code=422, detail={"code": "employee.invalid_ids", "fields": errors})
    return out


def _apply_sensitive(emp: Employee, values: dict[str, str | None]) -> None:
    if "tax_id" in values:
        emp.tax_id_encrypted = encrypt_pii(values["tax_id"])
        emp.tax_id_masked = mask_tail(values["tax_id"])
    if "taj" in values:
        emp.taj_encrypted = encrypt_pii(values["taj"])
        emp.taj_masked = mask_tail(values["taj"])
    if "bank_account" in values:
        emp.bank_account_encrypted = encrypt_pii(values["bank_account"])
        emp.bank_account_masked = mask_tail(values["bank_account"])
    if "wage_amount" in values:
        emp.wage_encrypted = encrypt_pii(values["wage_amount"])


async def _load_skills_map(
    db: AsyncSession, employee_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict]]:
    """{employee_id: [{id, name}, …]} — egy lekérdezésből."""
    if not employee_ids:
        return {}
    rows = (
        await db.execute(
            select(EmployeeSkill.employee_id, Skill.id, Skill.name)
            .join(Skill, Skill.id == EmployeeSkill.skill_id)
            .where(EmployeeSkill.employee_id.in_(employee_ids))
            .order_by(Skill.name)
        )
    ).all()
    out: dict[uuid.UUID, list[dict]] = {}
    for emp_id, skill_id, name in rows:
        out.setdefault(emp_id, []).append({"id": skill_id, "name": name})
    return out


async def _set_skills(db: AsyncSession, emp: Employee, skill_ids: list[int]) -> None:
    """A dolgozó skill-listájának teljes cseréje (csak létező skillekkel)."""
    valid_ids = set(
        (await db.execute(select(Skill.id).where(Skill.id.in_(skill_ids)))).scalars()
    )
    await db.execute(delete(EmployeeSkill).where(EmployeeSkill.employee_id == emp.id))
    for sid in valid_ids:
        db.add(EmployeeSkill(employee_id=emp.id, skill_id=sid))


def _employee_out(emp: Employee, email: str | None = None) -> EmployeeOut:
    return EmployeeOut(
        id=str(emp.id),
        user_id=str(emp.user_id),
        email=email,
        status=emp.status,
        last_name=emp.last_name,
        first_name=emp.first_name,
        birth_name=emp.birth_name,
        mother_name=emp.mother_name,
        birth_place=emp.birth_place,
        birth_date=emp.birth_date,
        citizenship=emp.citizenship,
        phone=emp.phone,
        address=emp.address,
        residence_address=emp.residence_address,
        hire_date=emp.hire_date,
        termination_date=emp.termination_date,
        job_title=emp.job_title,
        feor_code=emp.feor_code,
        employment_type=emp.employment_type,
        contract_type=emp.contract_type,
        weekly_hours=emp.weekly_hours,
        wage_type=emp.wage_type,
        annual_leave_days=emp.annual_leave_days,
        notes=emp.notes,
        tax_id_masked=emp.tax_id_masked,
        taj_masked=emp.taj_masked,
        bank_account_masked=emp.bank_account_masked,
        has_wage=emp.wage_encrypted is not None,
        employee_code=emp.employee_code,
    )


async def _get_or_404(db: AsyncSession, employee_id: str) -> Employee:
    try:
        eid = uuid.UUID(employee_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "employee.not_found"})
    emp = (await db.execute(select(Employee).where(Employee.id == eid))).scalar_one_or_none()
    if emp is None:
        raise HTTPException(status_code=404, detail={"code": "employee.not_found"})
    return emp


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.get("", response_model=list[EmployeeOut])
async def list_employees(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    rows = (
        await db.execute(
            select(Employee, User.email)
            .join(User, User.id == Employee.user_id)
            .order_by(Employee.last_name, Employee.first_name)
        )
    ).all()
    skills_map = await _load_skills_map(db, [emp.id for emp, _ in rows])
    out = []
    for emp, email in rows:
        item = _employee_out(emp, email)
        item.skills = skills_map.get(emp.id, [])
        out.append(item)
    return out


@router.post("", response_model=EmployeeOut, status_code=201)
async def create_employee(
    body: EmployeeCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    sensitive = _validate_sensitive(body)

    existing = (
        await db.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail={"code": "employee.email_taken"})

    password = body.initial_password or secrets.token_urlsafe(12)
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(password),
        display_name=f"{body.last_name} {body.first_name}",
        role="employee",
    )
    db.add(user)
    await db.flush()

    emp = Employee(
        user_id=user.id,
        employee_code=await generate_unique_employee_code(db),
        **body.model_dump(
            exclude={
                "email", "initial_password", "tax_id", "taj",
                "bank_account", "wage_amount", "skill_ids",
            }
        ),
    )
    _apply_sensitive(emp, sensitive)
    db.add(emp)
    await db.flush()
    if body.skill_ids:
        await _set_skills(db, emp, body.skill_ids)

    await record_audit(
        db, actor=actor, action="employee.create", entity_type="employee",
        entity_id=str(emp.id), request=request,
    )
    await db.commit()
    out = _employee_out(emp, user.email)
    out.skills = (await _load_skills_map(db, [emp.id])).get(emp.id, [])
    if body.initial_password is None:
        out.generated_password = password  # egyszeri megjelenítés az adminnak
    return out


@router.get("/{employee_id}", response_model=EmployeeOut)
async def get_employee(
    employee_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("manager")),
):
    emp = await _get_or_404(db, employee_id)
    user = (await db.execute(select(User).where(User.id == emp.user_id))).scalar_one_or_none()
    out = _employee_out(emp, user.email if user else None)
    out.skills = (await _load_skills_map(db, [emp.id])).get(emp.id, [])
    return out


@router.patch("/{employee_id}", response_model=EmployeeOut)
async def update_employee(
    employee_id: str,
    body: EmployeeUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    emp = await _get_or_404(db, employee_id)
    data = body.model_dump(exclude_unset=True)

    skill_ids = data.pop("skill_ids", None)
    sensitive_in = SensitiveFields(
        **{k: data.pop(k) for k in ("tax_id", "taj", "bank_account", "wage_amount") if k in data}
    )
    sensitive = _validate_sensitive(sensitive_in)

    if "status" in data and data["status"] not in ("active", "inactive"):
        raise HTTPException(status_code=422, detail={"code": "employee.bad_status"})

    changed = sorted(set(data) | set(sensitive) | ({"skills"} if skill_ids is not None else set()))
    for key, value in data.items():
        setattr(emp, key, value)
    _apply_sensitive(emp, sensitive)
    if skill_ids is not None:
        await _set_skills(db, emp, skill_ids)

    # Státuszváltás → a login-fiók (User) aktivitásának szinkronizálása.
    # Inaktiválás egyúttal a nyitott munkameneteket is érvényteleníti
    # (a get_current_user elutasítja a nem aktív usert).
    if "status" in data:
        emp_user = (
            await db.execute(select(User).where(User.id == emp.user_id))
        ).scalar_one_or_none()
        if emp_user is not None:
            emp_user.is_active = data["status"] == "active"

    await record_audit(
        db, actor=actor, action="employee.update", entity_type="employee",
        entity_id=str(emp.id), detail={"fields": changed}, request=request,
    )
    await db.commit()
    user = (await db.execute(select(User).where(User.id == emp.user_id))).scalar_one_or_none()
    out = _employee_out(emp, user.email if user else None)
    out.skills = (await _load_skills_map(db, [emp.id])).get(emp.id, [])
    return out


@router.get("/{employee_id}/reveal", response_model=RevealOut)
async def reveal_sensitive(
    employee_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    """Decrypted identifiers — admin only, every call audited."""
    emp = await _get_or_404(db, employee_id)
    await record_audit(
        db, actor=actor, action="employee.reveal", entity_type="employee",
        entity_id=str(emp.id), request=request,
    )
    await db.commit()
    return RevealOut(
        tax_id=decrypt_pii(emp.tax_id_encrypted),
        taj=decrypt_pii(emp.taj_encrypted),
        bank_account=decrypt_pii(emp.bank_account_encrypted),
        wage_amount=decrypt_pii(emp.wage_encrypted),
    )


class PasswordResetOut(BaseModel):
    generated_password: str


@router.post("/{employee_id}/reset-password", response_model=PasswordResetOut)
async def reset_password(
    employee_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    """Új ideiglenes jelszó a dolgozó login-fiókjához — egyszer látható, az admin
    adja át. A token_version növelésével minden korábbi munkamenet érvénytelen."""
    emp = await _get_or_404(db, employee_id)
    user = (
        await db.execute(select(User).where(User.id == emp.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail={"code": "employee.not_found"})
    new_password = secrets.token_urlsafe(12)
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    await record_audit(
        db, actor=actor, action="employee.reset_password", entity_type="user",
        entity_id=str(user.id), request=request,
    )
    await db.commit()
    return PasswordResetOut(generated_password=new_password)
