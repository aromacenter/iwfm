"""153. kör (x-presso jegyek, 2026-11-08):

- 883d89d0: az X-Learning teszt válaszai kevert sorrendben jelennek meg (nem
  mindig az első a helyes), a beadott indexek visszafejthetők; Telegram a
  dolgozónak a tananyag kiosztásáról (kézi és szerepkör szerinti).
- 973cd4bb: el nem végzett feladatok automatikus átütemezése a következő
  munkanapra, nyommal (eredeti határidő, csúszások száma, indok-napló),
  Telegram-értesítés; beállításban kapcsolható; személyenkénti kimutatás."""

from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import make_employee_record, make_user
from tests.test_round142_learning import pick
from tests.test_tasks import make_emp, payload


async def test_learning_options_shuffled_and_assignment_notification(client, manager, monkeypatch):
    from app.services.wfm import telegram as tg_mod

    _, mgr = manager
    sent: list[tuple[str, str]] = []

    async def fake_send_personal(db, employee, text):
        sent.append((employee.first_name, text))
        return True

    monkeypatch.setattr(tg_mod, "send_personal", fake_send_personal)
    a_user, a_h = await make_user(email="kevert-a@example.com", role="employee")
    a = await make_employee_record(a_user, first_name="Anna", telegram_chat_id="7001")

    res = await client.post("/api/learning/materials", json={"title": "Keverős anyag"}, headers=mgr)
    mat = res.json()
    qb = {"questions": [
        {"text": f"K{i}", "options": [{"text": "helyes", "correct": True}, {"text": "rossz1", "correct": False},
                                      {"text": "rossz2", "correct": False}, {"text": "rossz3", "correct": False}]}
        for i in range(8)
    ]}
    assert (await client.put(f"/api/learning/materials/{mat['id']}/questions", json=qb, headers=mgr)).status_code == 200

    # kiosztás → Telegram a dolgozónak (cím + határidő)
    due = (date.today() + timedelta(days=7)).isoformat()
    res = await client.post(f"/api/learning/materials/{mat['id']}/assign", json={"employee_ids": [str(a.id)], "due_date": due}, headers=mgr)
    assert res.status_code == 200 and res.json()["created"] == 1 and res.json()["notified"] == 1
    assert sent and sent[-1][0] == "Anna" and "Keverős anyag" in sent[-1][1] and "határidő" in sent[-1][1]

    aid = (await client.get("/api/learning/my", headers=a_h)).json()["items"][0]["id"]
    detail = (await client.get(f"/api/learning/my/{aid}", headers=a_h)).json()
    # a helyes válasz NEM mindig az első (8 kérdésnél a keverés gyakorlatilag biztosan mozdít)
    first_positions = [q["options"].index("helyes") for q in detail["questions"]]
    assert any(p != 0 for p in first_positions)
    # ugyanaz a sorrend újratöltéskor (a kitöltés közben nem ugrál)
    again = (await client.get(f"/api/learning/my/{aid}", headers=a_h)).json()
    assert [q["options"] for q in again["questions"]] == [q["options"] for q in detail["questions"]]
    # a „mindig az első” stratégia már nem jó
    res = await client.post(f"/api/learning/my/{aid}/attempt", json={"answers": {q["id"]: [0] for q in detail["questions"]}}, headers=a_h)
    assert res.status_code == 200 and res.json()["passed"] is False and res.json()["score_percent"] < 100
    # a megjelenített sorrend szerinti helyes indexekkel 100 % (új próbálkozás = új keverés)
    detail2 = (await client.get(f"/api/learning/my/{aid}", headers=a_h)).json()
    assert [q["options"] for q in detail2["questions"]] != [q["options"] for q in detail["questions"]]
    res = await client.post(f"/api/learning/my/{aid}/attempt", json={"answers": {q["id"]: pick(detail2, q["id"], "helyes") for q in detail2["questions"]}}, headers=a_h)
    assert res.status_code == 200 and res.json()["passed"] is True

    # szerepkör szerinti automatikus kiosztás is értesít (új belépő)
    sent.clear()
    assert (await client.post(f"/api/learning/materials/{mat['id']}/assign", json={"roles": ["employee"]}, headers=mgr)).status_code == 200
    b_user, b_h = await make_user(email="kevert-b@example.com", role="employee")
    await make_employee_record(b_user, first_name="Béla", telegram_chat_id="7002")
    assert len((await client.get("/api/learning/my", headers=b_h)).json()["items"]) == 1
    assert any(n == "Béla" and "Keverős anyag" in t for n, t in sent)


async def test_task_carry_over_moves_open_tasks_to_next_working_day(client, manager, monkeypatch):
    import app.db as app_db
    from app.services.wfm import telegram as tg_mod
    from app.services.wfm.task_carry_over import run_task_carry_over, target_day

    _, mgr = manager
    emp, emp_h = await make_emp(email="csuszo@example.com")
    sent: list[tuple[str, str]] = []

    async def fake_send_personal(db, employee, text):
        sent.append((str(employee.id), text))
        return True

    monkeypatch.setattr(tg_mod, "send_personal", fake_send_personal)
    async with app_db.get_session_factory()() as s:
        from sqlalchemy import update

        from app.models import Employee

        await s.execute(update(Employee).where(Employee.id == emp.id).values(telegram_chat_id="7100"))
        await s.commit()

    # munkanap-logika: péntek → péntek (ha munkanap, a mai), szombat/vasárnap → hétfő; ünnep átugorva
    assert target_day(date(2026, 11, 6)) == date(2026, 11, 6)
    assert target_day(date(2026, 11, 7)) == date(2026, 11, 9)
    assert target_day(date(2026, 11, 8)) == date(2026, 11, 9)
    assert target_day(date(2026, 5, 1)) == date(2026, 5, 4)  # péntek ünnep → hétfő
    assert target_day(date(2026, 1, 1)) == date(2026, 1, 5)  # jan. 2. áthelyezett pihenőnap (hídnap)

    today = date(2026, 11, 9)  # hétfő
    mk = lambda **kw: client.post("/api/tasks", json=payload(str(emp.id), **kw), headers=mgr)  # noqa: E731
    old1 = (await mk(title="Múlt heti szerviz", due_date="2026-11-05")).json()
    old2 = (await mk(title="Régi, több munka kell", due_date="2026-10-30")).json()
    done = (await mk(title="Kész volt", due_date="2026-11-06")).json()
    future = (await mk(title="Jövő heti", due_date="2026-11-12")).json()
    assert (await client.patch(f"/api/tasks/{old2['id']}", json={"status": "needs_more_work"}, headers=mgr)).status_code == 200
    assert (await client.patch(f"/api/tasks/{done['id']}", json={"status": "done"}, headers=mgr)).status_code == 200

    async with app_db.get_session_factory()() as s:
        out = await run_task_carry_over(s, today)
    assert out["moved"] == 2 and out["target"] == "2026-11-09" and out["notified"] == 1
    assert sent and "2 el nem végzett feladatod" in sent[-1][1] and "Múlt heti szerviz" in sent[-1][1]

    tasks = {t["id"]: t for t in (await client.get("/api/tasks", headers=mgr)).json()}
    t1, t2 = tasks[old1["id"]], tasks[old2["id"]]
    assert t1["due_date"] == "2026-11-09" and t1["carry_over_count"] == 1 and t1["original_due_date"] == "2026-11-05"
    assert "2026-11-05 → 2026-11-09" in (t1["reschedule_note"] or "") and "automatikus" in t1["reschedule_note"]
    assert t2["due_date"] == "2026-11-09" and t2["status"] == "needs_more_work" and t2["original_due_date"] == "2026-10-30"
    assert tasks[done["id"]]["due_date"] == "2026-11-06" and tasks[done["id"]]["carry_over_count"] == 0
    assert tasks[future["id"]]["due_date"] == "2026-11-12" and tasks[future["id"]]["carry_over_count"] == 0

    # ugyanaznap másodszor nincs mit mozgatni; másnap (kedd) a még mindig nyitott megint csúszik, az eredeti dátum marad
    async with app_db.get_session_factory()() as s:
        assert (await run_task_carry_over(s, today))["moved"] == 0
        assert (await run_task_carry_over(s, date(2026, 11, 10)))["moved"] == 2
    t1 = {t["id"]: t for t in (await client.get("/api/tasks", headers=mgr)).json()}[old1["id"]]
    assert t1["due_date"] == "2026-11-10" and t1["carry_over_count"] == 2 and t1["original_due_date"] == "2026-11-05"

    # vezérlőpult: személyenkénti kimutatás
    stats = (await client.get("/api/dashboard/carry-over", headers=mgr)).json()
    row = next(r for r in stats["employees"] if r["employee_id"] == str(emp.id))
    assert row["open_carried"] == 2 and row["carried_recent"] == 2 and row["oldest_original"] == "2026-10-30"
    assert stats["open_total"] == 2
    # a dolgozó jogosultság nélkül nem látja
    assert (await client.get("/api/dashboard/carry-over", headers=emp_h)).status_code == 403


async def test_carry_over_setting_and_notifier_hook(client, admin, monkeypatch):
    import app.db as app_db
    from app.services.wfm import notifier

    _, adm = admin
    # alapból BE; kikapcsolható; a napi futás tiszteli
    s = (await client.get("/api/settings/notifications", headers=adm)).json()
    assert s["task_carry_over"] is True
    body = {k: v for k, v in s.items() if k in ("daily_enabled", "recipients", "send_hour", "weekly_backup", "auto_receipt",
                                                 "wa_enabled", "wa_phone_id", "wa_recipients", "tg_enabled", "tg_chat_ids",
                                                 "tg_events", "weekly_digest", "weekly_day", "reminders_enabled", "reminder_days")}
    res = await client.put("/api/settings/notifications", json={**body, "task_carry_over": False}, headers=adm)
    assert res.status_code == 200, res.text
    assert (await client.get("/api/settings/notifications", headers=adm)).json()["task_carry_over"] is False

    calls: list = []

    async def fake_run(db, today, notify=True):
        calls.append(today)
        return {"moved": 0, "notified": 0, "target": today.isoformat()}

    import app.services.wfm.task_carry_over as tco

    monkeypatch.setattr(tco, "run_task_carry_over", fake_run)
    async with app_db.get_session_factory()() as db:
        out = await notifier.run_pending_notifications(db)
    assert out["carry_over"] == 0 and calls == []  # kikapcsolva nem fut

    res = await client.put("/api/settings/notifications", json={**body, "task_carry_over": True}, headers=adm)
    assert res.status_code == 200
    async with app_db.get_session_factory()() as db:
        await notifier.run_pending_notifications(db)
        await notifier.run_pending_notifications(db)
    assert len(calls) == 1  # naponta egyszer
