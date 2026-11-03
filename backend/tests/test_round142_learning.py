"""142. kör: X-Learning — tananyag + kérdésbank + csatolmány, kiosztás egyénileg és
szerepkör szerint, külsős tiltás, saját karton izoláció (idegen: 404), teszt
(részleges / 100 % → elvégezve + érvényesség), oklevél PDF, képzés-napló CSV,
Telegram-emlékeztető (3 nap, lejárt, érvényesség-lejárat), modulkapcsoló, jogok."""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime, timedelta

from tests.conftest import make_employee_record, make_user
from tests.test_parts import _enable_modules
from tests.test_tasks import make_emp

PDF_DATA_URL = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 teszt oktatoanyag").decode()


def _questions_body():
    return {"questions": [
        {"text": "Mi a teendő tűz esetén?", "options": [
            {"text": "Elfutok", "correct": False}, {"text": "Riasztok és oltok", "correct": True}, {"text": "Semmi", "correct": False},
        ]},
        {"text": "Melyik védőeszköz kötelező? (több is)", "options": [
            {"text": "Sisak", "correct": True}, {"text": "Papucs", "correct": False}, {"text": "Kesztyű", "correct": True},
        ]},
    ]}


async def test_learning_flow(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    a, a_h = await make_emp(email="tanulo-a@example.com")
    b, b_h = await make_emp(email="tanulo-b@example.com")
    c_user, _ = await make_user(email="kulsos@example.com", role="szervizes")
    c = await make_employee_record(c_user, is_contractor=True, last_name="Külsős", first_name="Kálmán")

    # anyag + kérdésbank + csatolmány
    res = await client.post("/api/learning/materials", json={
        "title": "Tűzvédelmi oktatás", "category": "kötelező", "body": "# Tűz\nRiasztás, oltás, menekülés.",
        "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "validity_months": 12,
    }, headers=mgr)
    assert res.status_code == 201, res.text
    mat = res.json()
    assert mat["video_id"] == "dQw4w9WgXcQ" and mat["question_count"] == 0
    bad = {"questions": [{"text": "Nincs helyes", "options": [{"text": "a"}, {"text": "b"}]}]}
    assert (await client.put(f"/api/learning/materials/{mat['id']}/questions", json=bad, headers=mgr)).status_code == 422
    res = await client.put(f"/api/learning/materials/{mat['id']}/questions", json=_questions_body(), headers=mgr)
    assert res.status_code == 200 and res.json()["count"] == 2
    res = await client.post(f"/api/learning/materials/{mat['id']}/attachments",
                            json={"filename": "tuzvedelem.pdf", "data_url": PDF_DATA_URL}, headers=mgr)
    assert res.status_code == 201, res.text
    att = res.json()
    assert (await client.post(f"/api/learning/materials/{mat['id']}/attachments",
                              json={"filename": "x.exe", "data_url": "data:application/octet-stream;base64,QUJD"}, headers=mgr)).status_code == 422
    listed = (await client.get("/api/learning/materials", headers=mgr)).json()
    assert listed[0]["question_count"] == 2 and len(listed[0]["attachments"]) == 1

    # kiosztás egyénileg; ismételt kiosztás nem duplikál; külsős csak external_ok-nál
    due = (date.today() + timedelta(days=5)).isoformat()
    res = await client.post(f"/api/learning/materials/{mat['id']}/assign", json={"employee_ids": [str(a.id)], "due_date": due}, headers=mgr)
    assert res.status_code == 200 and res.json() == {"created": 1, "already": 0}
    assert (await client.post(f"/api/learning/materials/{mat['id']}/assign", json={"employee_ids": [str(a.id)]}, headers=mgr)).json() == {"created": 0, "already": 1}
    res = await client.post(f"/api/learning/materials/{mat['id']}/assign", json={"employee_ids": [str(c.id)]}, headers=mgr)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "learning.external_not_allowed"
    assert (await client.patch(f"/api/learning/materials/{mat['id']}", json={"external_ok": True}, headers=mgr)).status_code == 200
    assert (await client.post(f"/api/learning/materials/{mat['id']}/assign", json={"employee_ids": [str(c.id)]}, headers=mgr)).json()["created"] == 1
    assert (await client.post(f"/api/learning/materials/{mat['id']}/assign", json={}, headers=mgr)).status_code == 422

    # saját karton: A látja, B nem — idegen azonosítóra 404 mindenhol
    my = (await client.get("/api/learning/my", headers=a_h)).json()
    assert len(my["items"]) == 1
    item = my["items"][0]
    assert item["status"] == "open" and item["has_test"] and item["has_video"] and item["due_date"] == due and item["source"] == "manual"
    aid = item["id"]
    assert (await client.get("/api/learning/my", headers=b_h)).json()["items"] == []
    assert (await client.get(f"/api/learning/my/{aid}", headers=b_h)).status_code == 404
    assert (await client.get(f"/api/learning/my/{aid}/attachments/{att['id']}", headers=b_h)).status_code == 404
    assert (await client.post(f"/api/learning/my/{aid}/attempt", json={"answers": {}}, headers=b_h)).status_code == 404
    assert (await client.get(f"/api/learning/my/{aid}/certificate.pdf", headers=b_h)).status_code == 404
    assert (await client.get("/api/learning/my/nem-uuid", headers=a_h)).status_code == 404

    # anyag a dolgozónak: kérdések helyes-jelölés nélkül, multi-flag, csatolmány letölthető
    detail = (await client.get(f"/api/learning/my/{aid}", headers=a_h)).json()
    assert detail["material"]["video_id"] == "dQw4w9WgXcQ" and detail["material"]["attachments"][0]["id"] == att["id"]
    qs = detail["questions"]
    assert len(qs) == 2 and "correct" not in str(qs) and [q["multi"] for q in qs] == [False, True]
    assert qs[0]["options"] == ["Elfutok", "Riasztok és oltok", "Semmi"]
    res = await client.get(f"/api/learning/my/{aid}/attachments/{att['id']}", headers=a_h)
    assert res.status_code == 200 and res.content.startswith(b"%PDF")

    # teszt: hiányos → 422; részleges → 50 %, nem elvégzett; oklevél még nincs
    q1, q2 = qs[0]["id"], qs[1]["id"]
    res = await client.post(f"/api/learning/my/{aid}/attempt", json={"answers": {q1: [1]}}, headers=a_h)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "learning.incomplete"
    res = await client.post(f"/api/learning/my/{aid}/attempt", json={"answers": {q1: [1], q2: [0]}}, headers=a_h)
    assert res.status_code == 200, res.text
    assert res.json()["score_percent"] == 50 and res.json()["passed"] is False and res.json()["status"] == "open"
    assert (await client.get(f"/api/learning/my/{aid}/certificate.pdf", headers=a_h)).status_code == 422

    # 100 % → elvégezve, érvényesség +12 hónap; oklevél PDF sorszámmal
    res = await client.post(f"/api/learning/my/{aid}/attempt", json={"answers": {q1: [1], q2: [2, 0]}}, headers=a_h)
    assert res.status_code == 200 and res.json()["passed"] is True and res.json()["status"] == "done"
    vu = date.fromisoformat(res.json()["valid_until"])
    today = date.today()
    assert (vu.year, vu.month) == (today.year + 1, today.month)
    res = await client.get(f"/api/learning/my/{aid}/certificate.pdf", headers=a_h)
    assert res.status_code == 200 and res.content.startswith(b"%PDF") and "oklevel_XL-" in res.headers["content-disposition"]
    assert res.headers["content-disposition"].startswith("attachment")
    res = await client.get(f"/api/learning/my/{aid}/certificate.pdf?inline=1", headers=a_h)
    assert res.status_code == 200 and res.headers["content-disposition"].startswith("inline")
    # elvégzett tananyag: újrapróbálás zárva
    res = await client.post(f"/api/learning/my/{aid}/attempt", json={"answers": {q1: [1], q2: [2, 0]}}, headers=a_h)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "learning.already_done"
    my = (await client.get("/api/learning/my", headers=a_h)).json()["items"][0]
    assert my["status"] == "done" and my["attempts"] == 2 and my["last_score"] == 100

    # vezetői eredmény-tábla, áttekintés, képzés-napló CSV (bérexport-jog)
    results = (await client.get(f"/api/learning/materials/{mat['id']}/results", headers=mgr)).json()
    by_emp = {r["employee_id"]: r for r in results}
    assert by_emp[str(a.id)]["status"] == "done" and by_emp[str(a.id)]["attempts"] == 2 and by_emp[str(a.id)]["best_score"] == 100
    assert by_emp[str(c.id)]["status"] == "open" and by_emp[str(c.id)]["is_contractor"] is True
    ov = (await client.get("/api/learning/overview", headers=mgr)).json()
    assert ov == {"materials": 1, "assigned": 2, "done": 1, "open": 1, "overdue": 0, "expired": 0}
    res = await client.get(f"/api/learning/export.csv?year={today.year}&month={today.month}", headers=adm)
    assert res.status_code == 200 and res.content.startswith("﻿".encode())
    text = res.content.decode("utf-8-sig")
    assert text.splitlines()[0] == "nev;tananyag;kategoria;elvegezve;ervenyes;kiosztas"
    assert f"Teszt Elek;Tűzvédelmi oktatás;kötelező;{today.isoformat()};{vu.isoformat()};manual" in text
    assert len(text.strip().splitlines()) == 2  # a külsős még nem végezte el

    # kiosztás visszavonása; jogok: dolgozó a vezetői részt nem éri el; törlés csak delete-joggal
    cid = by_emp[str(c.id)]["assignment_id"]
    assert (await client.delete(f"/api/learning/materials/{mat['id']}/assignments/{cid}", headers=mgr)).status_code == 204
    assert (await client.get("/api/learning/overview", headers=mgr)).json()["assigned"] == 1
    assert (await client.get("/api/learning/materials", headers=a_h)).status_code == 403
    assert (await client.get("/api/learning/export.csv?year=2026&month=1", headers=a_h)).status_code == 403
    assert (await client.delete(f"/api/learning/materials/{mat['id']}", headers=mgr)).status_code == 403
    assert (await client.delete(f"/api/learning/materials/{mat['id']}", headers=adm)).status_code == 204
    assert (await client.get("/api/learning/my", headers=a_h)).json()["items"] == []

    # modulkapcsoló: learning nélkül 403 mindenhol
    await _enable_modules(["employees", "tasks"])
    assert (await client.get("/api/learning/my", headers=a_h)).status_code == 403
    assert (await client.get("/api/learning/materials", headers=mgr)).status_code == 403


async def test_random_subset_and_role_assignment_and_reminders(client, manager, monkeypatch):
    import app.db as app_db
    from app.api.learning import run_learning_reminders
    from app.models import LearningAssignment
    from app.services.wfm import telegram as tg_mod

    _, mgr = manager
    a, a_h = await make_emp(email="szerep-a@example.com")
    c_user, c_h = await make_user(email="szerep-kulsos@example.com", role="szervizes")
    await make_employee_record(c_user, is_contractor=True, last_name="Alvállalkozó", first_name="Aladár")

    # véletlen 2 kérdés a 4-ből; szerepkör szerinti kiosztás alapértelmezett határidővel
    res = await client.post("/api/learning/materials", json={
        "title": "Gépkezelés", "random_count": 2, "default_due_days": 10,
    }, headers=mgr)
    mat = res.json()
    qb = {"questions": [
        {"text": f"K{i}", "options": [{"text": "jó", "correct": True}, {"text": "rossz", "correct": False}]} for i in range(4)
    ]}
    assert (await client.put(f"/api/learning/materials/{mat['id']}/questions", json=qb, headers=mgr)).status_code == 200
    res = await client.post(f"/api/learning/materials/{mat['id']}/assign", json={"roles": ["employee"]}, headers=mgr)
    assert res.status_code == 200 and res.json()["created"] == 1  # A kapja, a manager (nincs dolgozó-karton) és a külsős nem
    assert (await client.get("/api/learning/materials", headers=mgr)).json()[0]["auto_roles"] == ["employee"]

    # később belépő dolgozó automatikusan megkapja (source=role, határidő +10 nap)
    d, d_h = await make_emp(email="szerep-d@example.com")
    my_d = (await client.get("/api/learning/my", headers=d_h)).json()["items"]
    assert len(my_d) == 1 and my_d[0]["source"] == "role" and my_d[0]["due_date"] == (date.today() + timedelta(days=10)).isoformat()
    assert (await client.get("/api/learning/my", headers=c_h)).json()["items"] == []  # külsős: nem employee szerepkör

    # véletlen részhalmaz: 2 kérdés jön, 2 válasz elég a 100 %-hoz
    aid = my_d[0]["id"]
    detail = (await client.get(f"/api/learning/my/{aid}", headers=d_h)).json()
    assert len(detail["questions"]) == 2
    answers = {q["id"]: [0] for q in detail["questions"]}
    res = await client.post(f"/api/learning/my/{aid}/attempt", json={"answers": answers}, headers=d_h)
    assert res.status_code == 200 and res.json()["passed"] is True and res.json()["valid_until"] is None

    # emlékeztetők: A határideje 2 nap múlva → küldés; ugyanaznap másodszor nem; lejárt érvényesség → újra
    sent: list[tuple[str, str]] = []

    async def fake_personal(db, employee, text):
        sent.append((employee.email if hasattr(employee, "email") else str(employee.id), text))
        return True

    monkeypatch.setattr(tg_mod, "send_personal", fake_personal)
    today = date.today()
    factory = app_db.get_session_factory()
    async with factory() as s:
        from sqlalchemy import select

        row = (await s.execute(select(LearningAssignment).where(LearningAssignment.employee_id == a.id))).scalar_one()
        row.due_date = today + timedelta(days=2)
        await s.commit()
    async with factory() as s:
        assert await run_learning_reminders(s, today) == 1
    assert "határidő közeleg" in sent[0][1]
    async with factory() as s:
        assert await run_learning_reminders(s, today) == 0  # reminded_on véd
    async with factory() as s:
        row = (await s.execute(select(LearningAssignment).where(LearningAssignment.employee_id == a.id))).scalar_one()
        row.completed_at = datetime.now(UTC) - timedelta(days=400)
        row.valid_until = today - timedelta(days=1)
        row.reminded_on = None
        await s.commit()
    async with factory() as s:
        assert await run_learning_reminders(s, today) == 1
    assert "Lejárt az érvényessége" in sent[-1][1]
    assert (await client.get("/api/learning/my", headers=a_h)).json()["items"][0]["status"] == "expired"
    assert (await client.get("/api/learning/overview", headers=mgr)).json()["expired"] == 1


async def test_learning_plan_adds_to_user_limit(client, admin, monkeypatch):
    """142. kör: az X-Learning csomag tanulói fiókjai a fiók-limithez adódnak;
    a Flotta az operator-licencen küldi (learning_plan + learning_max_users),
    a példány licenc-státusza mutatja; korlátlan X-Learning = korlátlan fiók."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "operator_token", "flotta-titok")
    _, adm = admin
    op = {"X-Operator-Token": "flotta-titok"}
    base = {"plan": "s", "valid_until": None, "customer_name": "Demo Kft."}
    res = await client.put("/api/operator/license", json={**base, "learning_plan": "xl-s", "learning_max_users": 10}, headers=op)
    assert res.status_code == 200, res.text
    st = (await client.get("/api/settings/license/status", headers=adm)).json()
    assert st["plan"] == "s" and st["max_users"] == 12 and st["learning_plan"] == "xl-s" and st["learning_max_users"] == 10
    # korlátlan X-Learning csomag → korlátlan fiók
    res = await client.put("/api/operator/license", json={**base, "learning_plan": "xl-l", "learning_max_users": None}, headers=op)
    assert res.status_code == 200, res.text
    st = (await client.get("/api/settings/license/status", headers=adm)).json()
    assert st["max_users"] is None and st["learning_plan"] == "xl-l"
    # csomag levétele: vissza a sáv limitjére
    res = await client.put("/api/operator/license", json={**base, "learning_plan": None}, headers=op)
    assert res.status_code == 200
    st = (await client.get("/api/settings/license/status", headers=adm)).json()
    assert st["max_users"] == 2 and st["learning_plan"] is None


async def test_manager_own_card_and_self_assignment(client, admin, manager):
    """142. kör: vezetői fiók saját dolgozói kartont készít egy gombbal, aztán
    magának is kiosztható a tananyag; másodszor 409; dolgozó-jog nélkül 403."""
    _, adm = admin
    _, mgr = manager
    _, e_h = await make_emp(email="sima@example.com")
    assert (await client.get("/api/learning/my", headers=adm)).status_code == 404
    res = await client.post("/api/employees/me", headers=adm)
    assert res.status_code == 201, res.text
    own_id = res.json()["id"]
    assert (await client.post("/api/employees/me", headers=adm)).status_code == 409
    assert (await client.post("/api/employees/me", headers=e_h)).status_code == 403
    assert (await client.get("/api/learning/my", headers=adm)).json()["items"] == []
    mat = (await client.post("/api/learning/materials", json={"title": "Önképzés"}, headers=mgr)).json()
    assert (await client.post(f"/api/learning/materials/{mat['id']}/assign", json={"employee_ids": [own_id]}, headers=adm)).json()["created"] == 1
    my = (await client.get("/api/learning/my", headers=adm)).json()["items"]
    assert len(my) == 1 and my[0]["title"] == "Önképzés"
