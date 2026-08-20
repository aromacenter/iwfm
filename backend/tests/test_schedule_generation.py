"""Beosztás-generálás elérhetőségből: ünnep-kihagyás, ledolgozó szombati
auto-szabadság, meglévő műszak/távollét tisztelete. + holidays helper."""

from __future__ import annotations

from datetime import date

from tests.test_tasks import make_emp


def test_holiday_helpers():
    from app.services.wfm.holidays import (
        easter_sunday,
        is_bridge_rest_day,
        is_non_working_day,
        is_public_holiday,
        is_worked_saturday,
    )

    assert easter_sunday(2026) == date(2026, 4, 5)
    assert is_public_holiday(date(2026, 4, 6))   # húsvéthétfő
    assert is_public_holiday(date(2026, 4, 3))   # nagypéntek
    assert is_public_holiday(date(2026, 5, 25))  # pünkösdhétfő
    assert is_public_holiday(date(2026, 8, 20))
    assert not is_public_holiday(date(2026, 8, 19))
    # 2026-os rendelet: aug 21. áthelyezett pihenőnap, aug 8. ledolgozó szombat
    assert is_bridge_rest_day(date(2026, 8, 21))
    assert is_non_working_day(date(2026, 8, 21))
    assert is_worked_saturday(date(2026, 8, 8))
    assert not is_non_working_day(date(2026, 8, 8))


async def test_generate_week_from_availability(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    emp, _ = await make_emp(email="beosztott@example.com")

    # elérhetőség: hétfő–szerda 08–16 (a dolgozó-patch admin-jogos)
    res = await client.patch(
        f"/api/employees/{emp.id}",
        json={"availability": {"0": ["08:00", "16:00"], "1": ["08:00", "16:00"],
                               "2": ["08:00", "16:00"]}},
        headers=adm,
    )
    assert res.status_code == 200, res.text
    assert res.json()["availability"]["0"] == ["08:00", "16:00"]

    # sima hét (2026-09-07 hétfő, nincs ünnep): 3 vázlat készül
    res = await client.post(
        "/api/shifts/generate", json={"week_start": "2026-09-07"}, headers=mgr
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["created"] == 3
    assert out["skipped_holiday"] == []

    # újrafuttatva nem duplikál
    res = await client.post(
        "/api/shifts/generate", json={"week_start": "2026-09-07"}, headers=mgr
    )
    assert res.json()["created"] == 0
    assert res.json()["skipped_existing"] == 3

    # ünnepes hét: 2026-08-17 hét (aug 20. csütörtök ünnep, aug 21. péntek
    # áthelyezett pihenőnap) — ezekre nem tervez
    res = await client.post(
        "/api/shifts/generate", json={"week_start": "2026-08-17"}, headers=mgr
    )
    out = res.json()
    assert "2026-08-20" in out["skipped_holiday"]
    assert "2026-08-21" in out["skipped_holiday"]
    assert out["created"] == 3  # hétfő-szerda elérhetőség szerint

    # ledolgozó szombatos hét: 2026-08-03 hét (aug 8. szombat munkanap) —
    # automatikus szabadság-kiírás
    res = await client.post(
        "/api/shifts/generate", json={"week_start": "2026-08-03"}, headers=mgr
    )
    out = res.json()
    assert out["auto_leave"] >= 1
    # a kiírt szabadság tényleg létezik, jóváhagyva
    to = await client.get("/api/time-off", headers=mgr)
    assert any(
        r["start_date"] == "2026-08-08" and r["status"] == "approved"
        for r in to.json()
    )
