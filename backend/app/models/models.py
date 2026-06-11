"""Database models — Iwfm workforce management.

Sensitive employee identifiers (adóazonosító jel, TAJ szám, bankszámlaszám,
bér) are stored Fernet-encrypted in ``*_encrypted`` LargeBinary columns and
never in plaintext. See app/core/crypto.py.

Types are kept portable (sa.Uuid / JSON / LargeBinary) so the same models run
on PostgreSQL (Railway) and SQLite (local dev/tests).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class User(Base):
    """Login account. Every employee has one; admins/managers run the system."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("role IN ('admin','manager','employee')", name="ck_users_role"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="employee")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Employee(Base):
    """Employee master record (HR törzsadat).

    Field set covers what Hungarian payroll (bérszámfejtés) needs: személyes
    adatok, azonosítók (adóazonosító jel, TAJ), lakcím, bankszámla, munkaügyi
    adatok (FEOR, munkakör, heti óraszám, bér). Sensitive identifiers are
    encrypted at rest; list/detail responses expose only masked values unless
    the caller has admin rights and explicitly requests reveal (audited).
    """

    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_employees_user"),
        CheckConstraint(
            "employment_type IN ('full_time','part_time')", name="ck_employees_emp_type"
        ),
        CheckConstraint(
            "contract_type IN ('indefinite','fixed_term')", name="ck_employees_contract"
        ),
        CheckConstraint("wage_type IN ('monthly','hourly')", name="ck_employees_wage_type"),
        CheckConstraint("status IN ('active','inactive')", name="ck_employees_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    # --- személyes adatok ---
    last_name: Mapped[str] = mapped_column(String(128), nullable=False)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    birth_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    mother_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    birth_place: Mapped[str | None] = mapped_column(String(128), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    citizenship: Mapped[str] = mapped_column(String(64), nullable=False, default="magyar")

    # --- elérhetőség / cím ---
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(String(512), nullable=True)  # állandó lakcím
    residence_address: Mapped[str | None] = mapped_column(String(512), nullable=True)  # tartózkodási

    # --- érzékeny azonosítók (titkosítva) + maszkolt megjelenítés ---
    tax_id_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    tax_id_masked: Mapped[str | None] = mapped_column(String(16), nullable=True)
    taj_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    taj_masked: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bank_account_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    bank_account_masked: Mapped[str | None] = mapped_column(String(16), nullable=True)
    wage_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # --- munkaügyi adatok ---
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feor_code: Mapped[str | None] = mapped_column(String(8), nullable=True)  # FEOR-08
    employment_type: Mapped[str] = mapped_column(String(16), nullable=False, default="full_time")
    contract_type: Mapped[str] = mapped_column(String(16), nullable=False, default="indefinite")
    weekly_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    wage_type: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    annual_leave_days: Mapped[int] = mapped_column(Integer, nullable=False, default=20)  # Mt. 116.§ alapszabadság

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Shift(Base):
    """One scheduled shift. ``end_time <= start_time`` means it crosses midnight.

    Draft shifts are visible only to managers; publishing (Mt. 97.§ (4): at
    least 168h before the shift starts, for at least one full week) makes them
    visible to the employee.
    """

    __tablename__ = "shifts"
    __table_args__ = (
        CheckConstraint("status IN ('draft','published')", name="ck_shifts_status"),
        CheckConstraint("break_minutes >= 0", name="ck_shifts_break"),
        Index("ix_shifts_employee_date", "employee_id", "work_date"),
        Index("ix_shifts_date", "work_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    role_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TimeOffRequest(Base):
    """Szabadság / távollét kérelem."""

    __tablename__ = "time_off_requests"
    __table_args__ = (
        CheckConstraint(
            "type IN ('annual','sick','unpaid','other')", name="ck_timeoff_type"
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','cancelled')", name="ck_timeoff_status"
        ),
        CheckConstraint("end_date >= start_date", name="ck_timeoff_range"),
        Index("ix_timeoff_employee", "employee_id", "start_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="annual")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TimeEntry(Base):
    """Jelenléti bejegyzés (be-/kijelentkezés). Worked time = out - in - break."""

    __tablename__ = "time_entries"
    __table_args__ = (
        CheckConstraint("source IN ('self','manual')", name="ck_timeentry_source"),
        Index("ix_timeentry_employee", "employee_id", "clock_in"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    employee_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    clock_in: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    clock_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shift_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="self")
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    edited_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuditEvent(Base):
    """Append-only audit trail. Sensitive-data reveals, publishes, exports,
    and every mutation of employee PII are recorded here (GDPR accountability)."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_created", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. employee.reveal
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
