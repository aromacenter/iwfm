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
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
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
        CheckConstraint(
            "role IN ('admin','manager','uzletkoto','szervizes','employee')",
            name="ck_users_role",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="employee")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # A kiadott JWT-k érvényességi pecsétje: növelésekor minden korábbi token
    # érvénytelenné válik (jelszóváltás / kényszerített kijelentkeztetés).
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Elmentett aláírás-minta (PNG data URL) — munkalapon egy kattintással
    # beszúrható a dolgozói aláíráshoz.
    signature_sample: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Helyettes a távollét (jóváhagyott szabadság/betegség) idejére — a
    # felelős-képviselői értesítések ilyenkor hozzá futnak be.
    substitute_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL", name="fk_users_substitute"),
        nullable=True,
    )
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
        Index("uq_employees_code", "employee_code", unique=True),
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
    # 6 jegyű törzsszám — a blokkoló-terminál (kiosk) azonosítója.
    # Nullable a meglévő sorok migrációja miatt; induláskor backfill tölti.
    employee_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    # A dolgozó privát Telegram-csevegése (a botnak küldött törzsszámmal
    # kapcsolódik) — a személyre szóló értesítések (pl. kiosztott feladat) ide.
    telegram_chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

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
    # Bérszámfejtés alapja: 'attendance' = blokkolás (jelenlét) szerint,
    # 'schedule' = a közzétett beosztás szerint — dolgozónként kapcsolható.
    payroll_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="attendance"
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # Alvállalkozó (számlás) — nem munkaviszonyos: csak név/cím/elérhetőség/
    # adószám/bankszámla kell; bármikor átváltható alkalmazottira és vissza.
    is_contractor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Alvállalkozó adószáma (ha számlaképes) — nem titkosított, céges adat.
    company_tax_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Heti elérhetőség a beosztás-generáláshoz: {"0": ["08:00","16:00"], …}
    # — kulcs a hét napja (0=hétfő … 6=vasárnap), érték [tól, ig]. None = nincs
    # megadva (a generálás kihagyja).
    availability: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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


class Task(Base):
    """Kiosztott feladat. A required_skill a későbbi AI-alapú, skill szerinti
    kiosztást készíti elő; most a kiosztó felületen segít szűrni."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','done','needs_more_work')", name="ck_tasks_status"
        ),
        Index("ix_tasks_employee_due", "employee_id", "due_date"),
        Index("ix_tasks_due", "due_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    required_skill_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("skills.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TaskComment(Base):
    """A dolgozó (vagy vezető) megjegyzése a feladathoz."""

    __tablename__ = "task_comments"
    __table_args__ = (Index("ix_task_comments_task", "task_id", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Worksheet(Base):
    """Munkalap — egy feladathoz egy. A dolgozó tölti ki a telefonján
    (elvégzett munka, anyagok, óra, aláírások), a vezető PDF-ben letölti."""

    __tablename__ = "worksheets"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_worksheets_task"),
        UniqueConstraint("serial", name="uq_worksheets_serial"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    serial: Mapped[str] = mapped_column(String(20), nullable=False)  # ML-2026-0001 / KSZ-2026-0001
    # Külső szerviznek átadott gép munkalapja — külön sorszám-tartomány (KSZ-)
    # és külön nézet a sima munkalapoktól.
    external_service: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # A munkalap tárgy-gépe (KSZ-nél kötelező jellegű): ebből jön a
    # karbantartási díj alapértéke ÉS az auto-számlázás partnere.
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("assets.id", ondelete="SET NULL", name="fk_worksheets_asset"),
        nullable=True,
    )
    # Karbantartási díj (nettó): a gép díjából előtöltve, szabadon átírható.
    maintenance_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Kedvezmény: a díj elengedve — ilyenkor NEM készül automatikus számla.
    fee_discount: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Auto-számlázás nyoma (mindkét aláírás után): Billingó-bizonylat + e-mail.
    billingo_document_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    work_description: Mapped[str] = mapped_column(Text, nullable=False)
    # [{"name": "...", "qty": "...", "unit": "db"}, ...]
    materials: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Elvégzett munkák tételesen: [{name, price_net}] — soronként ár (nettó Ft).
    works: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # A KSZ ügyfél-példányára (−1) szánt megjegyzés — a képviselő írja; a
    # szervizes belső leírása SOSEM kerül az ügyfél elé.
    customer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Javítási konstrukciók (alternatív ajánlatok árral): [{name, cost_net,
    # price_net}] — a szervizes a saját díjával viszi fel, az ügyfél-példányra
    # a képviselő által beállított ár kerül.
    repair_options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Ügyfél-árajánlat a konstrukciókból: publikus link (token), állapot
    # (none → sent → accepted), és az ügyfél által kiválasztott opció neve.
    quote_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quote_status: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    quote_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    quote_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quote_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quote_selected_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    quote_accepted_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # A kész gépet elhoztuk a külsős szerelőtől — ekkor kap az ügyfél e-mailt,
    # hogy a javítás elkészült és a gép átvehető.
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Átadás az ügyfélnek: fizetési mód (cash/card), kedvezmény (= nem készül
    # számla), a fizetett nettó végösszeg és a Billingó-bizonylat. Az átadással
    # a munkalap lezárul, a feladat done lesz.
    handed_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handover_payment_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    handover_discount: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    handover_total_net: Mapped[float | None] = mapped_column(Float, nullable=True)
    handover_document_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hours_spent: Mapped[float | None] = mapped_column(Float, nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    client_location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # PNG data URL-ek a képernyős aláírásról (max ~200KB, szerveroldalon ellenőrizve)
    employee_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Az ügyfél-aláíráshoz KÖTELEZŐEN begépelt név — az azonosítás biztos.
    client_signer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Skill(Base):
    """Képesítés / készség (pl. targoncavezető, pénztár, elsősegély)."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("name", name="uq_skills_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EmployeeSkill(Base):
    """Dolgozó ↔ skill összerendelés."""

    __tablename__ = "employee_skills"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )


class EmailSettings(Base):
    """SMTP beállítások az értesítésekhez — egyetlen sor (id=1).
    A jelszó Fernet-titkosítva tárolódik, a GET sosem adja vissza."""

    __tablename__ = "email_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    host: Mapped[str | None] = mapped_column(String(256), nullable=True)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=587)
    username: Mapped[str | None] = mapped_column(String(256), nullable=True)
    password_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    from_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    use_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AISettings(Base):
    """AI szolgáltatók (Anthropic Claude / Google Gemini) — egyetlen sor (id=1).
    Az API kulcsok Fernet-titkosítva tárolódnak, a GET sosem adja vissza őket."""

    __tablename__ = "ai_settings"
    __table_args__ = (
        CheckConstraint(
            "active_provider IN ('none','anthropic','gemini')", name="ck_ai_provider"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active_provider: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    anthropic_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    anthropic_model: Mapped[str] = mapped_column(
        String(64), nullable=False, default="claude-opus-4-8"
    )
    gemini_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    gemini_model: Mapped[str] = mapped_column(
        String(64), nullable=False, default="gemini-3.5-flash"
    )
    # Szerkeszthető feladat-kiosztási prompt-sablon (None = beépített alapértelmezés)
    assign_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorksheetSettings(Base):
    """Munkalap-PDF testreszabás — egyetlen sor (id=1). Céglogo, fejléc/lábléc,
    akcentusszín és mező-kapcsolók a kiállított munkalap kinézetéhez."""

    __tablename__ = "worksheet_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    company_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    company_address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    footer_text: Mapped[str | None] = mapped_column(String(512), nullable=True)
    accent_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#1e40af")
    logo_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    logo_mime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    show_materials: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_hours: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_client_signature: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_comments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Az ügyfél-munkalap (−1) aljára kerülő garanciális feltételek — üresen a
    # beépített alapszöveg megy ki; adminból szerkeszthető.
    customer_footer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Az átvételi elismervény aljára kerülő záradék (60 napos tárolás) —
    # üresen a beépített alapszöveg; adminból szerkeszthető.
    intake_footer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Felmérési díj (nettó Ft) — az árajánlat "nem kérem a javítást"
    # opciójához; None = beépített alapérték (5000).
    survey_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MachineIntake(Base):
    """Gép-átvétel: az ügyfél behozott gépének rögzítése (tartozékok, hibák)
    + nyomtatható átvételi elismervény (AT-ÉÉÉÉ-NNNN)."""

    __tablename__ = "machine_intakes"
    __table_args__ = (UniqueConstraint("serial", name="uq_machine_intakes_serial"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    serial: Mapped[str] = mapped_column(String(20), nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("assets.id", ondelete="SET NULL", name="fk_intakes_asset"),
        nullable=True,
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="SET NULL", name="fk_intakes_partner"),
        nullable=True,
    )
    client_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    client_company: Mapped[str | None] = mapped_column(String(256), nullable=True)
    client_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    client_address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    accessories: Mapped[str | None] = mapped_column(Text, nullable=True)  # tartozékok
    faults: Mapped[str | None] = mapped_column(Text, nullable=True)  # hibaleírás
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_by_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL", name="fk_intakes_user"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LicenseSettings(Base):
    """Előfizetési licenc: sáv (S/M/L/XL), limitek és érvényesség. Egyetlen
    sor (id=1); sor nélkül a rendszer korlátlan — a saját, nem bérelt
    példányunk így fut."""

    __tablename__ = "license_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # s | m | l | xl — a sávok limitjeit a license szolgáltatás definiálja
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="xl")
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    # egyedi megállapodás esetén a sáv-limit felülírható
    max_users_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_employees_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Bekapcsolt extra modulok listája (billing/gls/labels/ai/portal/support).
    # NULL = MINDEN modul megy (a saját, X-Presso példány így fut — az új
    # modulok nála kapcsolgatás nélkül, azonnal élnek).
    enabled_modules: Mapped[list | None] = mapped_column(JSON, nullable=True)


class GlsSettings(Base):
    """MyGLS (GLS Hungary) integráció — egyetlen sor (id=1). A jelszó
    Fernet-titkosítva, write-only; a feladó-adatok a címkék PickupAddress-e."""

    __tablename__ = "gls_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    username: Mapped[str | None] = mapped_column(String(320), nullable=True)
    password_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    client_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    test_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Címke-formátum: A4_2x2 | A4_4x1 | Thermo (MyGLS TypeOfPrinter)
    printer_type: Mapped[str] = mapped_column(String(16), nullable=False, default="A4_2x2")
    sender_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sender_zip: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sender_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sender_street: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sender_house: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sender_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String(320), nullable=True)


class CourierSettings(Base):
    """Futár-hitelesítő adatok futáronként (mpl/foxpost/dpd) — a teljes
    konfiguráció Fernet-titkosított JSON, write-only."""

    __tablename__ = "courier_settings"

    carrier: Mapped[str] = mapped_column(String(16), primary_key=True)
    config_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class GlsParcel(Base):
    """Feladott csomag (történeti okból gls_parcels a tábla, de minden futár
    ide kerül): címzett, utánvét, csomagszám és a címke-PDF."""

    __tablename__ = "gls_parcels"
    __table_args__ = (Index("ix_gls_parcels_created", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    # gls | mpl | foxpost | dpd
    carrier: Mapped[str] = mapped_column(String(16), nullable=False, default="gls")
    # a futár belső azonosítója (törléshez/követéshez, ha nem a csomagszám)
    carrier_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("product_orders.id", ondelete="SET NULL", name="fk_gls_order"),
        nullable=True,
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="SET NULL", name="fk_gls_partner"),
        nullable=True,
    )
    recipient_name: Mapped[str] = mapped_column(String(256), nullable=False)
    recipient_zip: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient_city: Mapped[str] = mapped_column(String(128), nullable=False)
    recipient_street: Mapped[str] = mapped_column(String(256), nullable=False)
    recipient_house: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recipient_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    content: Mapped[str | None] = mapped_column(String(256), nullable=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cod_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    cod_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # XS (Exchange) szolgáltatás: a futár kézbesítéskor csere-csomagot hoz el
    exchange_service: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parcel_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # A MyGLS belső ParcelId-ja — a still-nem-átadott címke törléséhez kell.
    gls_parcel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    label_pdf: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Normalizált státusz: created (létrehozva) | handed_over (átadva
    # futárnak) | in_transit (szállítás alatt) | delivered (kiszállítva) |
    # returned (visszáru)
    status_key: Mapped[str] = mapped_column(String(24), nullable=False, default="created")
    last_status: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_status_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Teljes esemény-idővonal a GLS-től: [{date, description, depot, code}]
    status_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    test_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL", name="fk_gls_user"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PrintJob(Base):
    """Címkenyomtatási sor a Godex nyomtatóhoz. A felhő-backend nem éri el az
    üzleti hálózat nyomtatóját, ezért a nyomtatandó EZPL itt várakozik, amíg a
    helyi nyomtató-ügynök (agent/print_agent.ps1) le nem kéri és raw módban ki
    nem küldi a nyomtató 9100-as portjára."""

    __tablename__ = "print_jobs"
    __table_args__ = (Index("ix_print_jobs_status", "status", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="qr_label")
    # Emberi megnevezés a listákhoz, pl. "QR-címke ×3 (K-0012…)"
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # EZPL szöveg
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )  # pending | done | error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL", name="fk_print_jobs_user"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    printed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PrintSettings(Base):
    """A nyomtató-ügynök beállításai — egyetlen sor (id=1): a helyi ügynök
    hitelesítő kulcsa és az utolsó jelentkezésének ideje (online-jelzéshez)."""

    __tablename__ = "print_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    agent_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Partner(Base):
    """Partner cég, akihez eszközök helyezhetők ki."""

    __tablename__ = "partners"
    __table_args__ = (
        Index("ix_partners_name", "name"),
        Index("uq_partners_code", "partner_code", unique=True),
        Index("uq_partners_portal_token", "portal_token", unique=True),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Rövid, ember-olvasható ügyfél-azonosító (PT-0001) — automatikusan generált.
    partner_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)  # név / fantázianév
    # Hivatalos cégnév (számlázáshoz) — ha üres, a name-et használjuk.
    company_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Melyik SAJÁT cégünk szerződött vele / számláz neki: xp (X-Presso Coffee
    # Kft.) | pc (Premium Caffe Kft.) | None (nincs megadva).
    invoicing_company: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Partner-szintű szerződés — ha be van állítva, FELÜLÍRJA a gépeken lévő
    # feltételeket. Adag-alapú minimum + minimum alatti adagár (Ft, nettó):
    contract_min_portions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contract_below_min_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # VAGY kg-alapú minimum átvétel + minimum alatti ár (Ft/kg, nettó) — ha ez
    # be van állítva, az adag-alapú minimum helyett ez érvényesül:
    contract_min_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    contract_below_min_price_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Minimum-mentes (türelmi) időszak vége — pl. új partner első 3 hónapja.
    contract_no_min_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Elszámolás-gyakoriság tükre (1/2/4 hét) az aktív szerződésből.
    contract_settlement_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Az aktív szerződés fizetési módja/határideje (elszámolás-alapértelmezés).
    contract_payment_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    contract_payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Az aktív szerződés "nincs minimum" kapcsolójának tükre: igaz esetén a
    # partner pontosan a lefőzöttet fizeti (a gép-szintű minimumok sem élnek).
    contract_no_minimum: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Felelős képviselő (üzletkötő) — az ő dolga a partner igényeinek kezelése;
    # az automatizálások "képviselő" címzettje hozzá (távollétekor a
    # helyetteséhez) irányít.
    agent_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL", name="fk_partners_agent"),
        nullable=True,
    )
    # Ha igaz: a minimum el nem érésekor NEM különbözetet számlázunk, hanem a
    # gép(ek) fix havi bérleti díja lép életbe; minimum felett bérleti díj sincs.
    contract_rent_if_below_min: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # vevő (customer) | szállító (supplier) | mindkettő (both)
    partner_type: Mapped[str] = mapped_column(String(16), nullable=False, default="customer")
    tax_number: Mapped[str | None] = mapped_column(String(32), nullable=True)  # adószám
    eu_tax_number: Mapped[str | None] = mapped_column(String(32), nullable=True)  # közösségi adószám
    reg_number: Mapped[str | None] = mapped_column(String(64), nullable=True)  # cégjegyzékszám
    contact_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    website: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Összerakott (legacy) címek — a strukturált részekből számolódnak, ha azok
    # ki vannak töltve; a régi, egysoros adatot is megőrzik.
    address: Mapped[str | None] = mapped_column(String(512), nullable=True)  # székhely
    billing_address: Mapped[str | None] = mapped_column(String(512), nullable=True)  # számlázási cím
    # Strukturált székhely
    address_zip: Mapped[str | None] = mapped_column(String(16), nullable=True)
    address_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    address_street: Mapped[str | None] = mapped_column(String(256), nullable=True)
    address_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Strukturált számlázási cím
    billing_zip: Mapped[str | None] = mapped_column(String(16), nullable=True)
    billing_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    billing_street: Mapped[str | None] = mapped_column(String(256), nullable=True)
    billing_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Geokódolt székhely-koordináta (Nominatim/OSM) — az útvonaltervezés
    # használja; cím-mentéskor a háttérben frissül.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    bank_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # fizetési határidő (nap)
    # Partner-portál: kitalálhatatlan token a csak-olvasható nyilvános nézethez.
    portal_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Asset(Base):
    """Készleten lévő / kihelyezett eszköz (gép) egyedi vonalkóddal.

    status: in_stock (raktáron) | deployed (kihelyezve) | maintenance
            (szervizen) | retired (selejtezve). Kihelyezéskor partner_id +
            deployed_at töltődik, visszavételkor nullázódik.
    """

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("barcode", name="uq_assets_barcode"),
        Index("uq_assets_qr_token", "qr_token", unique=True),
        CheckConstraint(
            "status IN ('in_stock','deployed','maintenance','retired','handed_over')",
            name="ck_assets_status",
        ),
        Index("ix_assets_status", "status"),
        Index("ix_assets_partner", "partner_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)  # Típus (megnevezés)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)  # legacy
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)  # legacy
    manufacturer: Mapped[str | None] = mapped_column(String(128), nullable=True)  # gyártó
    article_number: Mapped[str | None] = mapped_column(String(64), nullable=True)  # cikkszám
    serial_number: Mapped[str | None] = mapped_column(String(128), nullable=True)  # gyárt. szám
    location_type: Mapped[str | None] = mapped_column(String(64), nullable=True)  # hely típus
    counter: Mapped[int | None] = mapped_column(Integer, nullable=True)  # számláló (összeg)
    # Több számlálós gépek: hány számláló van a gépen + az egyes állások.
    # A `counter` mindig az ÖSSZEG — a régi (norma, karbantartás, elszámolás)
    # logika változatlanul erre épül.
    counter_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    counters: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [állás1, állás2…]
    norm: Mapped[float | None] = mapped_column(Float, nullable=True)  # norma
    # Számlálónkénti norma (g kávé/adag) több számlálós gépnél — 0 = az adott
    # számláló terméke (pl. forró csoki) NEM használ kávét; a kg-
    # keresztellenőrzés ezzel súlyozza a lefőzött adagokat.
    norms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Melyik terméket (kávét) főzi a gép — a gép-soros elszámolás ez alapján
    # árazza az adagokat. None: a partner egyetlen készlet-terméke érvényes.
    default_product_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    tangible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # tárgyi eszköz
    # Az ügyfél SAJÁT gépe (mi csak szervizeljük) — nem "kihelyezett" saját eszköz.
    customer_owned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Szerződéses feltételek (nettó árak, az elszámolás automatikusan számolja):
    contract_min_portions: Mapped[int | None] = mapped_column(Integer, nullable=True)  # min. adag/hó
    contract_below_min_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # Ft/adag a különbözetre
    rent_fee: Mapped[float | None] = mapped_column(Float, nullable=True)  # bérleti díj Ft/hó (nettó)
    # Karbantartási díj (nettó Ft/alkalom) — a külsős karbantartási munkalapot
    # ez tölti elő; ott átírható, kedvezmény-pipával elengedhető.
    maintenance_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    # QR-címke: kitalálhatatlan token a nyilvános támogatási oldalhoz.
    qr_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="in_stock")
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="SET NULL"), nullable=True
    )
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AssetMovement(Base):
    """Eszköz mozgástörténet (append-only): létrehozás, kihelyezés, visszavétel,
    állapotváltás. Auditra és a kihelyezési előzményekhez."""

    __tablename__ = "asset_movements"
    __table_args__ = (Index("ix_asset_movements_asset", "asset_id", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(24), nullable=False)  # created|deploy|return|status
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="SET NULL"), nullable=True
    )
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PermissionSettings(Base):
    """Szerepkör → engedélyezett funkciók mátrix — egyetlen sor (id=1).
    A matrix JSON: {role: [feature, ...]}. Az admin mindig mindent lát/tehet
    (nem szerepel a mátrixban); a hiányzó szerepkörök a beépített
    alapértelmezést kapják (deps.DEFAULT_MATRIX)."""

    __tablename__ = "permission_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    matrix: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BillingoSettings(Base):
    """Billingó számlázó integráció — egyetlen sor (id=1). Az API-kulcs
    Fernet-titkosítva; test_mode=True esetén díjbekérő (proforma) készül,
    éles módban számla (NAV-jelentett!)."""

    __tablename__ = "billingo_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 1. fiók — X-Presso Coffee Kft. (xp)
    api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    block_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # számlatömb
    test_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 2. fiók — Premium Caffe Kft. (pc); a partner szerződött cége dönti el,
    # melyik fiókkal állítjuk ki a számlát.
    pc_api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    pc_block_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pc_test_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProductPriceLog(Base):
    """Termék-árváltozások naplója: mikor mennyi volt az eladási (adag-) és a
    beszerzési ár — visszakereshető, évek alatt statisztika lesz belőle."""

    __tablename__ = "product_price_logs"
    __table_args__ = (Index("ix_price_logs_product", "product_id", "changed_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("products.id", ondelete="CASCADE", name="fk_pricelog_product"),
        nullable=False,
    )
    price_per_portion: Mapped[float | None] = mapped_column(Float, nullable=True)
    purchase_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Product(Base):
    """Bizományba kihelyezhető fogyóeszköz (pl. kávé). kg-ban töltjük fel a
    partner külső raktárába; a gramm/adag alapján számoljuk az adagszámot és
    az elszámoláskor adag × ár/adag alapon számlázunk."""

    __tablename__ = "products"
    __table_args__ = (Index("ix_products_name", "name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # Csoportosítás (pl. Kávék, Kávégépek, Alkatrészek, Kellékek) — szabad
    # szöveg, a felület a meglévő értékekből ajánl.
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="kg")  # feltöltés egysége
    grams_per_portion: Mapped[int] = mapped_column(Integer, nullable=False, default=7)  # 1 adag = X g
    price_per_portion: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # Ft/adag (nettó)
    vat_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=27)  # ÁFA %
    # Bizományos termék (kávé): fogyás alapján számoljuk el. Minden más
    # termék ELADÁS — átadáskor/kiszállításkor azonnal fizetendő.
    is_consignment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Riasztási küszöb (kg): ha egy partnernél ennyi vagy kevesebb van, jelzünk.
    low_stock_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Utolsó beszerzési ár (Ft/kg, nettó) — bevételezéskor frissül, az űrlapot előtölti.
    purchase_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PartnerStock(Base):
    """A partner külső raktárában lévő aktuális (könyv szerinti) készlet egy
    termékből, a termék egységében (pl. kg). Feltöltéskor nő, elszámoláskor a
    fizikai leltárra állítjuk (a különbség a fogyás)."""

    __tablename__ = "partner_stock"
    __table_args__ = (
        UniqueConstraint("partner_id", "product_id", name="uq_partner_stock"),
        Index("ix_partner_stock_partner", "partner_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    partner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # egységben (kg)
    # Partnerenkénti riasztási küszöb (kg) — ha None, a termék globális
    # low_stock_threshold-ja érvényes.
    min_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PartnerPrice(Base):
    """Partner-specifikus ár-felülírás egy termékre (Ft/adag, nettó).
    Ha nincs sor, a termék alapára érvényes."""

    __tablename__ = "partner_prices"
    __table_args__ = (
        UniqueConstraint("partner_id", "product_id", name="uq_partner_price"),
        Index("ix_partner_prices_partner", "partner_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    partner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    price_per_portion: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class StockMovement(Base):
    """Partner-készlet mozgástörténet (append-only): feltöltés (+) és elszámolás
    szerinti fogyás (−). Auditra és a készletváltozások követésére."""

    __tablename__ = "stock_movements"
    __table_args__ = (Index("ix_stock_movements_partner", "partner_id", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    partner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # replenish | settlement
    quantity_delta: Mapped[float] = mapped_column(Float, nullable=False)  # + feltöltés, − fogyás
    # Beszerzési ár a bevételezéskor (Ft/kg, nettó) — csak replenish sorokon.
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    settlement_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("settlements.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Settlement(Base):
    """Egy partner (ügyfél) elszámolása: a fogyás kiszámlázása. Rögzíti az
    elszámoló nevét (bejelentkezett user), a fizetési módot és a Billingó-számla
    állapotát. A tételek a settlement_lines-ban."""

    __tablename__ = "settlements"
    __table_args__ = (
        CheckConstraint(
            "payment_method IN ('cash','card','transfer','cod')", name="ck_settlement_payment"
        ),
        Index("ix_settlements_partner", "partner_id", "created_at"),
        Index("ix_settlements_user", "settled_by_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    partner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False
    )
    settled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    settled_by_name: Mapped[str] = mapped_column(String(256), nullable=False)  # pillanatkép
    # Számlázó cég pillanatképe (a partner szerződött cége rögzítéskor): xp|pc.
    invoicing_company: Mapped[str | None] = mapped_column(String(8), nullable=True)
    payment_method: Mapped[str] = mapped_column(String(16), nullable=False)  # cash|card|transfer
    # ÁFA és számla nélküli elszámolás: a megadott (nettó) ár a fizetendő,
    # Billingó-számla nem készül hozzá.
    no_vat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    total_net: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_gross: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    invoiced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    billingo_document_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    billingo_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # created|error|none
    # Kintlévőség: none (nincs számlázva) | paid | outstanding
    payment_status: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # fizetési határidő
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A partner képernyős aláírása a bizonylaton (PNG data URL)
    partner_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Az aláíráshoz KÖTELEZŐEN begépelt név (ki írta alá a partnernél).
    signer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    receipt_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Tartozás-görgetés: a partner nyitott egyenlege az elszámolás ELŐTT
    # (pillanatkép) + a helyszínen ténylegesen fizetett összeg (bruttó Ft).
    # paid_amount=None → "nem rögzített" = a saját bruttója rendezettnek számít
    # (a régi elszámolások nem keletkeztetnek visszamenőleg tartozást).
    debt_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    paid_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SettlementMachine(Base):
    """Gép-soros elszámolási adat (a régi Xpresso-munkafolyamat tükre): egy gép
    előző/új számlálója, a levont szerviz-adagok és a kedvezmény pillanatképe.
    A számlázott adagok a gép termékének (default_product) során összegződnek."""

    __tablename__ = "settlement_machines"
    __table_args__ = (
        Index("ix_settlement_machines_settlement", "settlement_id"),
        Index("ix_settlement_machines_asset", "asset_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    settlement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("settlements.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    barcode: Mapped[str] = mapped_column(String(64), nullable=False)  # pillanatkép
    asset_name: Mapped[str] = mapped_column(String(256), nullable=False)  # pillanatkép
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    product_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    prev_counter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_counter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Szerviz közben lefőzött, NEM számlázandó adagok (összevont mező).
    service_portions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    portions_billed: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    price_per_portion: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    amount_net: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SettlementLine(Base):
    """Elszámolási tétel: egy termék fogyása és kiszámlázott értéke (adag alapon)."""

    __tablename__ = "settlement_lines"
    __table_args__ = (Index("ix_settlement_lines_settlement", "settlement_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    settlement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("settlements.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    product_name: Mapped[str] = mapped_column(String(256), nullable=False)  # pillanatkép
    previous_qty: Mapped[float] = mapped_column(Float, nullable=False)  # könyv szerinti (kg)
    physical_qty: Mapped[float] = mapped_column(Float, nullable=False)  # fizikai leltár (kg)
    consumed_qty: Mapped[float] = mapped_column(Float, nullable=False)  # fogyás (kg)
    portions: Mapped[float] = mapped_column(Float, nullable=False)  # adag
    # A gép számlálója szerinti adagszám az időszakban — ha meg van adva,
    # a számlázás EZ alapján megy, a kg-leltár pedig keresztellenőrzés.
    counter_portions: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_per_portion: Mapped[float] = mapped_column(Float, nullable=False)  # pillanatkép Ft/adag
    vat_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=27)
    amount_net: Mapped[float] = mapped_column(Float, nullable=False)  # portions * price


class ServiceTicket(Base):
    """Szerviz-hibajegy vagy karbantartási munka egy géphez (vagy általános).

    kind: repair (hibajavítás) | maintenance (karbantartás)
    status: open → in_progress → done (vagy cancelled)
    A gép- és partnernév pillanatképként is tárolódik, hogy törlés után is
    olvasható maradjon a jegy. counter_at_service: a gép számláló-állása a
    munka elvégzésekor — a karbantartás-esedékesség ebből számolódik.
    """

    __tablename__ = "service_tickets"
    __table_args__ = (
        CheckConstraint("kind IN ('repair','maintenance')", name="ck_service_kind"),
        CheckConstraint(
            "status IN ('open','in_progress','done','cancelled')", name="ck_service_status"
        ),
        CheckConstraint("priority IN ('low','normal','high')", name="ck_service_priority"),
        Index("ix_service_status", "status", "created_at"),
        Index("ix_service_asset", "asset_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticket_no: Mapped[str] = mapped_column(String(16), nullable=False)  # SZ-0001
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="repair")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    priority: Mapped[str] = mapped_column(String(8), nullable=False, default="normal")
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    asset_label: Mapped[str | None] = mapped_column(String(256), nullable=True)  # pillanatkép
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="SET NULL"), nullable=True
    )
    partner_label: Mapped[str | None] = mapped_column(String(256), nullable=True)  # pillanatkép
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    assigned_to_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    counter_at_service: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Szerviz-költség: felhasznált alkatrészek [{name, qty, unit_price}] + munkadíj (nettó).
    parts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    labor_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TicketAttachment(Base):
    """Szervizjegyhez csatolt kép (ügyfél-bejelentésből vagy belső feltöltésből).
    A kép bináris adata a DB-ben él (kis darabszám, egyszerű mentés/backup)."""

    __tablename__ = "ticket_attachments"
    __table_args__ = (Index("ix_ticket_attachments_ticket", "ticket_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("service_tickets.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SupportSettings(Base):
    """Ügyfél-támogatás beállításai — egyetlen sor (id=1). A tudásbázis szabad
    szöveg (markdown), ebből válaszol az AI chat a nyilvános QR-oldalon."""

    __tablename__ = "support_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    knowledge_base: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Új gép rögzítésekor az AI automatikusan generál hibakód-szekciót a
    # tudásbázisba, ha a gyártó+típus még nem szerepel benne.
    auto_kb: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProductOrder(Base):
    """A partner termékrendelése a nyilvános QR-oldalról. Az üzletkötő az
    Elszámolás oldalon látja, és a következő látogatáskor teljesíti."""

    __tablename__ = "product_orders"
    __table_args__ = (
        CheckConstraint("status IN ('open','done','cancelled')", name="ck_order_status"),
        Index("ix_orders_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_no: Mapped[str] = mapped_column(String(16), nullable=False)  # R-0001
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="SET NULL"), nullable=True
    )
    partner_label: Mapped[str | None] = mapped_column(String(256), nullable=True)  # pillanatkép
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    items: Mapped[list] = mapped_column(JSON, nullable=False)  # [{name, quantity, unit}]
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KnowledgeEntry(Base):
    """Kézzel rögzített, bevált megoldás egy géptípushoz (házi tudás, ami
    nincs benne a kézikönyvekben). A QR-oldali AI chat a tudásbázis-szöveg
    mellett ezekből is válaszol — a machine_label alapján a gépre szűrve."""

    __tablename__ = "knowledge_entries"
    __table_args__ = (Index("ix_knowledge_machine", "machine_label"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    machine_label: Mapped[str] = mapped_column(String(256), nullable=False)  # pl. "Jura XJ6"
    title: Mapped[str] = mapped_column(String(256), nullable=False)  # pl. "E8 gyakori oka nálunk"
    content: Mapped[str] = mapped_column(Text, nullable=False)  # a bevált megoldás
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual|ai
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EmailTemplate(Base):
    """Újrafelhasználható e-mail sablon ({{helyettesítőkkel}}) — az
    automatizálások „e-mail küldése" akciója használja."""

    __tablename__ = "email_templates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AutomationRule(Base):
    """Flow-szerű automatizálás: trigger (esemény) + feltételek + akciók.

    conditions: [{"field": "...", "op": "eq|ne|gte|lte|contains", "value": ...}]
      — mind teljesüljön (ÉS kapcsolat).
    actions: [{"type": "send_email", "template_id": "...", "to": "partner|recipients|cím1,cím2"},
              {"type": "add_partner_note", "text": "... {{valtozo}} ..."},
              {"type": "create_task", "title": "...", "description": "...", "employee": "név?"}]
    """

    __tablename__ = "automation_rules"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    trigger: Mapped[str] = mapped_column(String(64), nullable=False)  # pl. settlement.created
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    conditions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NotificationSettings(Base):
    """Értesítések — egyetlen sor (id=1). Napi összefoglaló e-mail (esedékes
    elszámolások, kifogyó készletek, nyitott rendelések, anomáliák), heti
    automatikus mentés-küldés, és aláírás utáni automatikus bizonylat-email."""

    __tablename__ = "notification_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    daily_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recipients: Mapped[str | None] = mapped_column(Text, nullable=True)  # e-mailek vesszővel
    send_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    weekly_backup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_receipt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # WhatsApp (Meta Cloud API) — belső kommunikációra és dolgozói push-ra.
    wa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    wa_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    wa_phone_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wa_recipients: Mapped[str | None] = mapped_column(Text, nullable=True)  # telefonszámok vesszővel
    # Telegram (Bot API) — csoport/csevegés-azonosítókra küldött push.
    tg_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tg_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    tg_chat_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # chat_id-k vesszővel
    # Mely eseményekről menjen beépített Telegram-értesítés (esemény-kulcsok
    # vesszővel, pl. "order.created,stock.low"); None/üres = egyikről sem.
    tg_events: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A getUpdates folytatási pontja (a dolgozói összekapcsoló-figyelőhöz).
    tg_update_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Naptár-frissítés (következő évi munkarend) utolsó AI-próbája (ÉÉÉÉ-HH).
    cal_last_check: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # Havi Mt.-változás-ellenőrzés: utolsó futás hónapja + utolsó eredmény.
    mt_last_check: Mapped[str | None] = mapped_column(String(7), nullable=True)
    mt_last_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Utolsó sikeres küldések (duplikátum-védelem újraindításkor)
    last_daily_sent: Mapped[str | None] = mapped_column(String(10), nullable=True)  # ÉÉÉÉ-HH-NN
    last_backup_sent: Mapped[str | None] = mapped_column(String(10), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AssetPhoto(Base):
    """Fotó egy gépről (kihelyezéskor / szervizkor / állapotrögzítéshez)."""

    __tablename__ = "asset_photos"
    __table_args__ = (Index("ix_asset_photos_asset", "asset_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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


class DeliveryNote(Base):
    """Digitális szállítólevél: áru kiszállítása egy partnerhez ELSZÁMOLÁS
    NÉLKÜL — bármelyik képviselő viheti bármelyik partnernek. A tételek a
    partner KÖVETKEZŐ elszámolásán töltődnek be és kerülnek kiszámlázásra
    (a bizonylaton/számlán SZL-hivatkozással). status: open → settled;
    cancelled = visszavont (nem számlázódik)."""

    __tablename__ = "delivery_notes"
    __table_args__ = (
        UniqueConstraint("serial", name="uq_delivery_notes_serial"),
        CheckConstraint(
            "status IN ('open','settled','cancelled')", name="ck_delivery_notes_status"
        ),
        Index("ix_delivery_notes_partner", "partner_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    serial: Mapped[str] = mapped_column(String(20), nullable=False)  # SZL-2026-0001
    partner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("partners.id", ondelete="CASCADE", name="fk_delivery_notes_partner"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    # Melyik saját raktárból (autóból) ment ki az áru — a készlet levonódott.
    source_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("warehouses.id", ondelete="SET NULL", name="fk_delivery_notes_wh"),
        nullable=True,
    )
    settlement_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("settlements.id", ondelete="SET NULL", name="fk_delivery_notes_settlement"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL", name="fk_delivery_notes_user"),
        nullable=True,
    )
    created_by_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DeliveryNoteLine(Base):
    """Szállítólevél-tétel: termék + mennyiség + rögzített nettó egységár
    (elszámoláskor ezzel számlázódik)."""

    __tablename__ = "delivery_note_lines"
    __table_args__ = (Index("ix_delivery_note_lines_note", "delivery_note_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    delivery_note_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("delivery_notes.id", ondelete="CASCADE", name="fk_dn_lines_note"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="SET NULL", name="fk_dn_lines_product"),
        nullable=True,
    )
    product_name: Mapped[str] = mapped_column(String(256), nullable=False)  # pillanatkép
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="db")
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)  # Ft/egység, nettó
    vat_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=27)


class PartnerContract(Base):
    """Partner-szerződés — a partnertől KÜLÖN kezelt feltétel-készlet,
    érvényességi idővel. Egy partnernek több szerződése lehet (történet +
    jövőbeli); az elszámolás mindig az adott napon érvényeset alkalmazza.
    A partneren lévő contract_* mezők a mindenkor aktív szerződés tükrei
    (apply_active_contract szinkronizálja) — a számítási kód ezekre épül."""

    __tablename__ = "partner_contracts"
    __table_args__ = (
        Index("ix_partner_contracts_partner", "partner_id", "valid_from"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    partner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("partners.id", ondelete="CASCADE", name="fk_partner_contracts_partner"),
        nullable=False,
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)  # None = határozatlan
    # Feltételek (a korábbi partner-mezők megfelelői):
    min_portions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    below_min_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # Ft/adag
    min_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    below_min_price_kg: Mapped[float | None] = mapped_column(Float, nullable=True)  # Ft/kg
    rent_if_below_min: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Elszámolás gyakorisága hetekben (1/2/4) — alapértelmezés 4 hetente,
    # azonos napokon; az esedékesség-lista ebből ütemez.
    settlement_weeks: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, server_default="4"
    )
    # Szerződéses fizetési mód (cash/card/transfer/cod) és határidő — az
    # elszámolásnál ez az alapértelmezés, ott csak felülírni lehet.
    payment_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Nincs minimum-elvárás: a partner mindig pontosan a lefőzöttet fizeti
    # (korlátlan türelmi időszak — a gép-szintű minimumokat is kikapcsolja).
    no_minimum: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL", name="fk_partner_contracts_user"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Warehouse(Base):
    """Saját raktár: telephely (site) vagy üzletkötői autó (van). Az áruút:
    beszerzés → telephely → autó → partner külső raktára (feltöltés)."""

    __tablename__ = "warehouses"
    __table_args__ = (
        CheckConstraint("kind IN ('site','van')", name="ck_warehouses_kind"),
        Index("ix_warehouses_name", "name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False, default="site")
    # Autó raktárnál: melyik felhasználó (üzletkötő) autója.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WarehouseStock(Base):
    """Aktuális készlet egy raktárban, termékenként (a termék egységében)."""

    __tablename__ = "warehouse_stock"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "product_id", name="uq_warehouse_stock"),
        Index("ix_warehouse_stock_wh", "warehouse_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Riasztási/újrarendelési küszöb ebben a raktárban (egységben).
    min_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WarehouseMovement(Base):
    """Raktári készletmozgás (append-only). action: receive (bevét) |
    transfer_out / transfer_in (áthelyezés) | issue (kiadás partnernek,
    feltöltéskor) | adjust (leltár-korrekció)."""

    __tablename__ = "warehouse_movements"
    __table_args__ = (Index("ix_warehouse_movements_wh", "warehouse_id", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity_delta: Mapped[float] = mapped_column(Float, nullable=False)  # + be, − ki
    # Bevételezési egységár (Ft/egység, nettó) — receive sorokon.
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Kapcsolódó entitás: áthelyezésnél a másik raktár, kiadásnál a partner,
    # bevételezésnél a beszerzési rendelés azonosítója.
    ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WarehouseTransfer(Base):
    """Függő raktár-átadás (telephely↔autó): a mozgást a FOGADÓ félnek jóvá
    kell hagynia. Indításkor a forrásról lekerül a mennyiség (úton van), a
    cél-raktárra csak elfogadáskor könyvelődik; elutasításkor/visszavonáskor
    visszakerül a forrásra. Autóra menő átadást csak az autóhoz rendelt
    üzletkötő fogadhat el; autóról telephelyre menőt a raktáros — a küldő a
    sajátját sosem hagyhatja jóvá."""

    __tablename__ = "warehouse_transfers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','accepted','rejected','cancelled')",
            name="ck_wh_transfers_status",
        ),
        Index("ix_wh_transfers_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    from_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )
    to_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL", name="fk_wh_transfers_creator"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL", name="fk_wh_transfers_decider"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_note: Mapped[str | None] = mapped_column(String(512), nullable=True)


class PurchaseOrder(Base):
    """Beszerzési rendelés egy szállítótól (Partner, partner_type supplier/both)
    egy cél-raktárba. Státusz: draft → ordered → received; cancelled."""

    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','ordered','received','cancelled')",
            name="ck_purchase_orders_status",
        ),
        Index("uq_purchase_orders_no", "order_no", unique=True),
        Index("ix_purchase_orders_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_no: Mapped[str] = mapped_column(String(16), nullable=False)  # PO-0001
    supplier_partner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="SET NULL"), nullable=True
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    expected_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PurchaseOrderLine(Base):
    """Beszerzési rendelés tétele. received_qty a részteljesítést követi."""

    __tablename__ = "purchase_order_lines"
    __table_args__ = (Index("ix_purchase_order_lines_po", "purchase_order_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("purchase_orders.id", ondelete="CASCADE", name="fk_po_lines_po"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("products.id", ondelete="CASCADE", name="fk_po_lines_product"),
        nullable=False,
    )
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)  # Ft/egység, nettó
    received_qty: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class QuoteMachineType(Base):
    """Ajánlatkérő gép-katalógus: a publikus árajánlat-oldalon választható
    géptípusok leírással ("mit tud") és célcsoporttal ("kiknek a legjobb")."""

    __tablename__ = "quote_machine_types"
    __table_args__ = (Index("ix_quote_machine_types_active", "active", "sort_order"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # mit tud
    ideal_for: Mapped[str | None] = mapped_column(Text, nullable=True)  # kiknek ajánljuk
    # Gép-fotó a publikus oldalra (PNG/JPEG/WebP, max 2 MB):
    image_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    image_mime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class QuoteRequest(Base):
    """Publikus árajánlat-kérés → szerződés → aláírás → partner.

    A vevő a nyilvános űrlapon adja meg a cégadatait és a kívánt gépet;
    mi töltjük ki az adagárat és a dátumokat, ebből készül a szerződés
    szövege; az aláíró-linket (sign_token) emailben küldjük. Aláírás után
    automatikusan létrejön a Partner + PartnerContract (partner_id).
    status: new → sent → signed; cancelled."""

    __tablename__ = "quote_requests"
    __table_args__ = (
        UniqueConstraint("serial", name="uq_quote_requests_serial"),
        Index("uq_quote_requests_sign_token", "sign_token", unique=True),
        CheckConstraint(
            "status IN ('new','sent','signed','cancelled')",
            name="ck_quote_requests_status",
        ),
        Index("ix_quote_requests_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    serial: Mapped[str] = mapped_column(String(20), nullable=False)  # AJ-2026-0001
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    # A vevő által megadott adatok (a későbbi Partner minden kötelező mezője):
    company_name: Mapped[str] = mapped_column(String(256), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(256), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(320), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    tax_number: Mapped[str] = mapped_column(String(32), nullable=False)
    address_zip: Mapped[str] = mapped_column(String(16), nullable=False)
    address_city: Mapped[str] = mapped_column(String(128), nullable=False)
    address_street: Mapped[str] = mapped_column(String(256), nullable=False)
    address_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    billing_zip: Mapped[str | None] = mapped_column(String(16), nullable=True)
    billing_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    billing_street: Mapped[str | None] = mapped_column(String(256), nullable=True)
    billing_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bank_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    machine_type_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("quote_machine_types.id", ondelete="SET NULL", name="fk_quotes_mtype"),
        nullable=True,
    )
    machine_type_name: Mapped[str | None] = mapped_column(String(256), nullable=True)  # pillanatkép
    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # vevő üzenete
    # Általunk kitöltött ajánlati/szerződéses feltételek (nettó árak):
    price_per_portion: Mapped[float | None] = mapped_column(Float, nullable=True)  # adagár Ft
    min_portions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    below_min_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    rent_if_below_min: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Melyik üzletkötőhöz tartozik az ügylet (admin rendeli hozzá):
    agent_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL", name="fk_quotes_agent"),
        nullable=True,
    )
    # Szerződés + aláírás:
    contract_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sign_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)  # PNG data URL
    signed_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    signed_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("partners.id", ondelete="SET NULL", name="fk_quotes_partner"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class QuoteSettings(Base):
    """Szerződés-sablon az ajánlatkérés-folyamathoz — egyetlen sor (id=1).
    contract_template: {{változós}} szöveg; None = beépített alapértelmezés."""

    __tablename__ = "quote_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    contract_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AgentExpense(Base):
    """Képviselői költség — a helyszíni munkával járó kiadás (üzemanyag,
    parkolás, kellék), amely az üzletkötő-elszámolásban a kasszájából
    (készpénzes bevételéből) levonódik."""

    __tablename__ = "agent_expenses"
    __table_args__ = (
        Index("ix_agent_expenses_user", "user_id", "expense_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE", name="fk_agent_expenses_user"),
        nullable=False,
    )
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_gross: Mapped[float] = mapped_column(Float, nullable=False)  # Ft, bruttó
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL", name="fk_agent_expenses_creator"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CalendarOverride(Base):
    """Évenkénti munkarend-átcsoportosítás (áthelyezett pihenőnapok +
    ledolgozó szombatok) — a holidays.py beégetett tábláját egészíti ki.
    Évente az AI tölti fel automatikusan (source='ai'), az admin a
    Beállításokban ellenőrzi/javítja (source='manual')."""

    __tablename__ = "calendar_overrides"
    __table_args__ = (UniqueConstraint("year", name="uq_calendar_overrides_year"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    rest_days: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # ["ÉÉÉÉ-HH-NN"]
    worked_saturdays: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # ai | manual
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
