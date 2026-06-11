"""AI feladat-kiosztás: kandidátus-gyűjtés, prompt, parse, endpoint-hibák."""

from datetime import date, timedelta

import app.db as app_db
from app.services.wfm.ai_assign import (
    Candidate,
    build_prompt,
    collect_candidates,
    parse_suggestion,
)
from tests.conftest import make_employee_record, make_user


# ─── parse_suggestion (tiszta függvény) ──────────────────────────────────────


def test_parse_plain_json():
    out = parse_suggestion(
        '{"employee_id": "abc-123", "reason": "Ő ér rá és van skillje."}', {"abc-123"}
    )
    assert out == {"employee_id": "abc-123", "reason": "Ő ér rá és van skillje."}


def test_parse_with_markdown_fences():
    text = '```json\n{"employee_id": "abc-123", "reason": "indok"}\n```'
    assert parse_suggestion(text, {"abc-123"})["employee_id"] == "abc-123"


def test_parse_with_surrounding_prose():
    text = 'A választásom:\n{"employee_id": "abc-123", "reason": "indok"}\nRemélem segít!'
    assert parse_suggestion(text, {"abc-123"}) is not None


def test_parse_rejects_unknown_id():
    assert parse_suggestion('{"employee_id": "hacker-id", "reason": "x"}', {"abc-123"}) is None


def test_parse_rejects_garbage():
    assert parse_suggestion("nem tudom eldönteni", {"abc-123"}) is None
    assert parse_suggestion('{"broken json', {"abc-123"}) is None


# ─── build_prompt ────────────────────────────────────────────────────────────


def test_prompt_contains_candidates_and_flags():
    candidates = [
        Candidate(
            id="id-1", name="Kovács Anna", job_title="eladó",
            skills=["pénztár"], has_required_skill=True, on_leave=False, open_tasks=2,
        ),
        Candidate(
            id="id-2", name="Nagy Péter", job_title="raktáros",
            skills=[], has_required_skill=False, on_leave=True, open_tasks=0,
        ),
    ]
    prompt = build_prompt(
        candidates,
        title="Polcrendezés",
        description="C sor",
        due_date=date(2026, 7, 1),
        required_skill_name="targoncavezető",
    )
    assert "Kovács Anna" in prompt and "id-1" in prompt
    assert "RENDELKEZIK a szükséges skillel" in prompt
    assert "NEM rendelkezik" in prompt
    assert "AZNAP SZABADSÁGON VAN" in prompt
    assert "nyitott feladatai: 2" in prompt
    assert "employee_id" in prompt  # JSON formátum-utasítás


# ─── collect_candidates (valós DB) ───────────────────────────────────────────


async def test_collect_candidates_flags(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    user1, _ = await make_user(email="kand1@example.com", role="employee")
    emp1 = await make_employee_record(user1, last_name="Skilles")
    user2, _ = await make_user(email="kand2@example.com", role="employee")
    emp2 = await make_employee_record(user2, last_name="Szabin")

    skill = (
        await client.post("/api/settings/skills", json={"name": "targonca"}, headers=adm)
    ).json()
    await client.patch(
        f"/api/employees/{emp1.id}", json={"skill_ids": [skill["id"]]}, headers=adm
    )

    due = date.today() + timedelta(days=3)
    rid = (
        await client.post(
            "/api/time-off",
            json={
                "employee_id": str(emp2.id), "type": "annual",
                "start_date": due.isoformat(), "end_date": due.isoformat(),
            },
            headers=mgr,
        )
    ).json()["id"]
    await client.patch(f"/api/time-off/{rid}/decide", json={"status": "approved"}, headers=mgr)

    await client.post(
        "/api/tasks",
        json={"title": "Meglévő munka", "employee_id": str(emp1.id), "due_date": due.isoformat()},
        headers=mgr,
    )

    factory = app_db.get_session_factory()
    async with factory() as session:
        candidates = await collect_candidates(
            session, due_date=due, required_skill_id=skill["id"]
        )
    by_name = {c.name.split()[0]: c for c in candidates}
    assert by_name["Skilles"].has_required_skill is True
    assert by_name["Skilles"].open_tasks == 1
    assert by_name["Szabin"].has_required_skill is False
    assert by_name["Szabin"].on_leave is True


# ─── endpoint hibaágak ───────────────────────────────────────────────────────


async def test_suggest_requires_ai_config(client, manager):
    _, mgr = manager
    user, _ = await make_user(email="kand3@example.com", role="employee")
    await make_employee_record(user)
    res = await client.post(
        "/api/tasks/suggest-assignee",
        json={"title": "Teszt", "due_date": date.today().isoformat()},
        headers=mgr,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "settings.ai_not_configured"


async def test_suggest_no_candidates(client, manager):
    _, mgr = manager
    res = await client.post(
        "/api/tasks/suggest-assignee",
        json={"title": "Teszt", "due_date": date.today().isoformat()},
        headers=mgr,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "tasks.no_candidates"


async def test_suggest_manager_only(client, employee_user):
    _, emp_headers, _ = employee_user
    res = await client.post(
        "/api/tasks/suggest-assignee",
        json={"title": "Teszt", "due_date": date.today().isoformat()},
        headers=emp_headers,
    )
    assert res.status_code == 403
