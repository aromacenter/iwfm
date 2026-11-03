"""GDPR adatmegőrzés: lejárt audit-sorok / fotók / képernyőképek takarítása,
védett audit-műveletek megőrzése, admin-only végpontok, napi hurok-őr."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

import app.db as app_db
from app.core.config import get_settings
from app.models import (
    AuditEvent,
    BugReport,
    IntakePhoto,
    MachineIntake,
    Task,
    Worksheet,
    WorksheetPhoto,
)

NOW = datetime.now(UTC)
OLD = NOW - timedelta(days=100)
FRESH = NOW - timedelta(days=1)


def _set_retention(monkeypatch, audit="30", photo="30", screenshot="30"):
    monkeypatch.setenv("WFM_RETENTION_AUDIT_DAYS", audit)
    monkeypatch.setenv("WFM_RETENTION_PHOTO_DAYS", photo)
    monkeypatch.setenv("WFM_RETENTION_SCREENSHOT_DAYS", screenshot)
    get_settings.cache_clear()
    s = get_settings()
    assert (s.retention_audit_days, s.retention_photo_days, s.retention_screenshot_days) == (
        int(audit), int(photo), int(screenshot)
    )


async def _seed(emp) -> dict:
    """Régi + friss sorok minden kategóriában; visszaadja az azonosítókat."""
    factory = app_db.get_session_factory()
    ids: dict[str, uuid.UUID] = {}
    async with factory() as s:
        # audit: régi törölhető, régi védett (3 prefix), friss törölhető
        rows = {
            "audit_old": AuditEvent(action="partner.create", entity_type="partner", created_at=OLD),
            "audit_old_anon": AuditEvent(action="employee.anonymize", entity_type="employee", created_at=OLD),
            "audit_old_admin": AuditEvent(action="admin.wipe", entity_type="assets", created_at=OLD),
            "audit_old_ret": AuditEvent(action="retention.run", entity_type="retention", created_at=OLD),
            "audit_new": AuditEvent(action="partner.update", entity_type="partner", created_at=FRESH),
        }
        s.add_all(rows.values())
        await s.flush()
        for k, r in rows.items():
            ids[k] = r.id

        intake = MachineIntake(serial="AT-2099-0001", client_name="Teszt Ügyfél")
        s.add(intake)
        task = Task(title="Javítás", employee_id=emp.id, due_date=date(2025, 1, 1))
        s.add(task)
        await s.flush()
        ws = Worksheet(task_id=task.id, serial="ML-2099-0001", work_description="x")
        s.add(ws)
        await s.flush()
        ids["intake"] = intake.id
        ids["worksheet"] = ws.id

        ip_old = IntakePhoto(intake_id=intake.id, image=b"\xff\xd8old", created_at=OLD)
        ip_new = IntakePhoto(intake_id=intake.id, image=b"\xff\xd8new", created_at=FRESH)
        wp_old = WorksheetPhoto(worksheet_id=ws.id, image=b"\xff\xd8old", created_at=OLD)
        wp_new = WorksheetPhoto(worksheet_id=ws.id, image=b"\xff\xd8new", created_at=FRESH)
        s.add_all([ip_old, ip_new, wp_old, wp_new])
        await s.flush()
        ids["ip_old"], ids["ip_new"] = ip_old.id, ip_new.id
        ids["wp_old"], ids["wp_new"] = wp_old.id, wp_new.id

        bug_old = BugReport(
            page_url="https://x/a", description="régi hiba leírás", severity="minor",
            screenshot=b"\x89PNG old", screenshot_mime="image/png", created_at=OLD,
        )
        bug_new = BugReport(
            page_url="https://x/b", description="friss hiba leírás", severity="minor",
            screenshot=b"\x89PNG new", screenshot_mime="image/png", created_at=FRESH,
        )
        bug_old_noshot = BugReport(
            page_url="https://x/c", description="régi, kép nélkül", severity="minor", created_at=OLD,
        )
        s.add_all([bug_old, bug_new, bug_old_noshot])
        await s.flush()
        ids["bug_old"], ids["bug_new"], ids["bug_old_noshot"] = bug_old.id, bug_new.id, bug_old_noshot.id
        await s.commit()
    return ids


async def _count(model, *where) -> int:
    async with app_db.get_session_factory()() as s:
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def _get(model, id_):
    async with app_db.get_session_factory()() as s:
        return await s.get(model, id_)


async def test_retention_endpoints_admin_only(client, manager):
    _, mgr = manager
    assert (await client.get("/api/admin/retention", headers=mgr)).status_code == 403
    assert (await client.post("/api/admin/retention/run", headers=mgr)).status_code == 403
    assert (await client.get("/api/admin/retention")).status_code in (401, 403)


async def test_retention_run_deletes_old_keeps_new_and_protected(client, admin, employee_user, monkeypatch):
    _, adm = admin
    _, _, emp = employee_user
    _set_retention(monkeypatch, audit="30", photo="30", screenshot="30")
    ids = await _seed(emp)

    status = (await client.get("/api/admin/retention", headers=adm)).json()
    assert status["enabled"] is True
    assert status["settings"] == {"audit_days": 30, "photo_days": 30, "screenshot_days": 30}
    # a seedelt, 100 napos "retention.run" sor az eddigi utolsó futás
    assert status["last_run"]["at"].startswith(OLD.strftime("%Y-%m-%d"))

    res = await client.post("/api/admin/retention/run", headers=adm)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ran"] is True
    assert body["counts"] == {
        "audit_events": 1,          # csak partner.create régi
        "intake_photos": 1,
        "worksheet_photos": 1,
        "bug_screenshots": 1,       # a kép nélküli régi jegy nem számít
    }

    # audit: a régi, nem védett törölve; a védettek és a friss megmaradtak
    assert await _get(AuditEvent, ids["audit_old"]) is None
    for k in ("audit_old_anon", "audit_old_admin", "audit_old_ret", "audit_new"):
        assert await _get(AuditEvent, ids[k]) is not None, k

    # fotók
    assert await _get(IntakePhoto, ids["ip_old"]) is None
    assert await _get(IntakePhoto, ids["ip_new"]) is not None
    assert await _get(WorksheetPhoto, ids["wp_old"]) is None
    assert await _get(WorksheetPhoto, ids["wp_new"]) is not None
    # a szülő átvétel / munkalap megmarad, a photo_count konzisztens (számolt)
    assert await _get(MachineIntake, ids["intake"]) is not None
    assert await _get(Worksheet, ids["worksheet"]) is not None
    intakes = (await client.get("/api/intakes", headers=adm)).json()
    assert next(r for r in intakes if r["serial"] == "AT-2099-0001")["photo_count"] == 1

    # hibajegyek: a régi megmarad kép nélkül, a friss képpel
    old_bug = await _get(BugReport, ids["bug_old"])
    assert old_bug is not None and old_bug.screenshot is None and old_bug.screenshot_mime is None
    assert old_bug.description == "régi hiba leírás"
    new_bug = await _get(BugReport, ids["bug_new"])
    assert new_bug.screenshot == b"\x89PNG new" and new_bug.screenshot_mime == "image/png"
    assert await _get(BugReport, ids["bug_old_noshot"]) is not None

    # audit-nyom: rendszer "retention.run" + admin "admin.retention_run"
    assert await _count(AuditEvent, AuditEvent.action == "retention.run", AuditEvent.created_at > FRESH) == 1
    assert await _count(AuditEvent, AuditEvent.action == "admin.retention_run") == 1

    status = (await client.get("/api/admin/retention", headers=adm)).json()
    assert status["last_run"] is not None
    assert status["last_run"]["counts"] == body["counts"]

    # másodszor: nincs már mit törölni
    again = (await client.post("/api/admin/retention/run", headers=adm)).json()
    assert again["counts"] == {"audit_events": 0, "intake_photos": 0, "worksheet_photos": 0, "bug_screenshots": 0}


async def test_retention_disabled_deletes_nothing(client, admin, employee_user, monkeypatch):
    _, adm = admin
    _, _, emp = employee_user
    _set_retention(monkeypatch, audit="0", photo="0", screenshot="0")
    ids = await _seed(emp)
    before_audit = await _count(AuditEvent)

    status = (await client.get("/api/admin/retention", headers=adm)).json()
    assert status["enabled"] is False

    res = await client.post("/api/admin/retention/run", headers=adm)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ran"] is False
    assert body["counts"] == {"audit_events": 0, "intake_photos": 0, "worksheet_photos": 0, "bug_screenshots": 0}

    for k in ("audit_old", "audit_old_anon", "audit_old_admin", "audit_old_ret", "audit_new"):
        assert await _get(AuditEvent, ids[k]) is not None, k
    assert await _count(IntakePhoto) == 2 and await _count(WorksheetPhoto) == 2
    old_bug = await _get(BugReport, ids["bug_old"])
    assert old_bug.screenshot == b"\x89PNG old"
    # nincs retention.run sor; az admin kattintása viszont naplózott
    assert await _count(AuditEvent, AuditEvent.action == "retention.run", AuditEvent.created_at > FRESH) == 0
    assert await _count(AuditEvent) == before_audit + 1
    assert await _count(AuditEvent, AuditEvent.action == "admin.retention_run") == 1


async def test_retention_partial_settings(client, admin, employee_user, monkeypatch):
    """Csak a képernyőkép-megőrzés van bekapcsolva: audit és fotó érintetlen."""
    _, adm = admin
    _, _, emp = employee_user
    _set_retention(monkeypatch, audit="0", photo="0", screenshot="7")
    ids = await _seed(emp)

    body = (await client.post("/api/admin/retention/run", headers=adm)).json()
    assert body["ran"] is True
    assert body["counts"] == {"audit_events": 0, "intake_photos": 0, "worksheet_photos": 0, "bug_screenshots": 1}
    assert await _get(AuditEvent, ids["audit_old"]) is not None
    assert await _count(IntakePhoto) == 2 and await _count(WorksheetPhoto) == 2
    assert (await _get(BugReport, ids["bug_old"])).screenshot is None


async def test_daily_hook_runs_once_per_day(client, employee_user, monkeypatch):
    """A háttérhurok őre: kikapcsolva nem fut; bekapcsolva naponta egyszer."""
    from app.services.wfm import notifier

    _, _, emp = employee_user
    monkeypatch.setattr(notifier, "_retention_last_run", None)
    factory = app_db.get_session_factory()

    _set_retention(monkeypatch, audit="0", photo="0", screenshot="0")
    assert await notifier.maybe_run_retention(factory) is False

    _set_retention(monkeypatch, audit="30", photo="30", screenshot="30")
    await _seed(emp)
    t0 = datetime.now(UTC)
    assert await notifier.maybe_run_retention(factory, now=t0) is True
    assert await _count(AuditEvent, AuditEvent.action == "retention.run", AuditEvent.created_at > FRESH) == 1
    # 23 óra múlva még nem, 25 óra múlva igen
    assert await notifier.maybe_run_retention(factory, now=t0 + timedelta(hours=23)) is False
    assert await notifier.maybe_run_retention(factory, now=t0 + timedelta(hours=25)) is True
    assert await _count(AuditEvent, AuditEvent.action == "retention.run", AuditEvent.created_at > FRESH) == 2

    # hiba a futásban nem dob kifelé
    async def boom(*a, **k):
        raise RuntimeError("db down")

    from app.services.wfm import retention

    monkeypatch.setattr(retention, "run_retention", boom)
    assert await notifier.maybe_run_retention(factory, now=t0 + timedelta(hours=50)) is False
