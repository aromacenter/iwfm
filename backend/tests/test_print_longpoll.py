"""Nyomtató-ügynök long-poll (v3): várakozó feladatnál azonnal válaszol,
üres sornál legfeljebb ``wait`` másodpercig tart nyitva."""

from __future__ import annotations

import time

from tests.test_print_jobs import _make_asset


async def test_agent_longpoll(client, admin, manager):
    _, adm = admin
    _, mgr = manager

    key = (await client.post("/api/print-jobs/agent-key", headers=adm)).json()["key"]
    agent = {"X-Agent-Key": key}

    # üres sor + wait=2: a kérés ~2 mp-ig tart, üres listával tér vissza
    t0 = time.monotonic()
    res = await client.get("/api/print-agent/jobs?wait=2", headers=agent)
    elapsed = time.monotonic() - t0
    assert res.status_code == 200
    assert res.json()["jobs"] == []
    assert elapsed >= 1.5  # tényleg várt (nem pörgött azonnal vissza)

    # várakozó feladatnál AZONNAL válaszol, hiába kér hosszú wait-et
    asset = await _make_asset(client, mgr, "LP-1")
    await client.post("/api/print-jobs", json={"ids": [asset["id"]]}, headers=mgr)
    t0 = time.monotonic()
    res = await client.get("/api/print-agent/jobs?wait=25", headers=agent)
    elapsed = time.monotonic() - t0
    assert res.status_code == 200
    assert len(res.json()["jobs"]) == 1
    assert elapsed < 1.5  # nem várta ki a 25 mp-et

    # a wait felső korlátja 25 — túlzó értéket is elfogad, de levágja
    res = await client.get("/api/print-agent/jobs?wait=9999", headers=agent)
    assert res.status_code == 200
    assert len(res.json()["jobs"]) == 1
