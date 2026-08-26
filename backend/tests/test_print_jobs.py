"""Godex nyomtatási sor: sorba állítás, ügynök-kulcs, ügynök-lekérés és
visszajelzés, online-állapot."""

from __future__ import annotations


async def _make_asset(client, mgr, barcode="PRN-1"):
    res = await client.post(
        "/api/assets",
        json={"barcode": barcode, "name": "Nyomtatós Gép", "manufacturer": "Saeco"},
        headers=mgr,
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


async def test_print_queue_flow(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    asset = await _make_asset(client, mgr, "PRN-1")

    # sorba állítás
    res = await client.post("/api/print-jobs", json={"ids": [asset["id"]]}, headers=mgr)
    assert res.status_code == 200, res.text
    job = res.json()
    assert job["status"] == "pending"
    assert "PRN-1" in (job["label"] or "")

    # ügynök kulcs nélkül / rossz kulccsal nem fér hozzá
    res = await client.get("/api/print-agent/jobs")
    assert res.status_code == 401
    res = await client.get("/api/print-agent/jobs", headers={"X-Agent-Key": "rossz"})
    assert res.status_code == 401

    # kulcs-generálás csak adminnak
    res = await client.post("/api/print-jobs/agent-key", headers=mgr)
    assert res.status_code in (401, 403)
    res = await client.post("/api/print-jobs/agent-key", headers=adm)
    assert res.status_code == 200, res.text
    key = res.json()["key"]
    agent = {"X-Agent-Key": key}

    # az ügynök lekéri a várakozó feladatot — EZPL-lel
    res = await client.get("/api/print-agent/jobs", headers=agent)
    assert res.status_code == 200, res.text
    jobs = res.json()["jobs"]
    assert [j["id"] for j in jobs] == [job["id"]]
    assert "^Q25,3" in jobs[0]["payload"]  # 51×25 mm-es EZPL
    assert "^L" in jobs[0]["payload"]

    # a lekérés után a felületen "ügynök online"
    q = (await client.get("/api/print-jobs", headers=mgr)).json()
    assert q["agent_configured"] is True
    assert q["agent_online"] is True

    # sikeres nyomtatás visszajelzése → done, a sor kiürül
    res = await client.post(
        f"/api/print-agent/jobs/{job['id']}", json={"ok": True}, headers=agent
    )
    assert res.status_code == 200
    assert (await client.get("/api/print-agent/jobs", headers=agent)).json()["jobs"] == []
    q = (await client.get("/api/print-jobs", headers=mgr)).json()
    done = next(j for j in q["jobs"] if j["id"] == job["id"])
    assert done["status"] == "done"
    assert done["printed_at"] is not None


async def test_print_job_error_reported(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    asset = await _make_asset(client, mgr, "PRN-2")
    job = (
        await client.post("/api/print-jobs", json={"ids": [asset["id"]]}, headers=mgr)
    ).json()
    key = (await client.post("/api/print-jobs/agent-key", headers=adm)).json()["key"]
    agent = {"X-Agent-Key": key}

    res = await client.post(
        f"/api/print-agent/jobs/{job['id']}",
        json={"ok": False, "error": "a nyomtató nem érhető el"},
        headers=agent,
    )
    assert res.status_code == 200
    q = (await client.get("/api/print-jobs", headers=mgr)).json()
    row = next(j for j in q["jobs"] if j["id"] == job["id"])
    assert row["status"] == "error"
    assert "nyomtató" in row["error"]


async def test_customer_owned_label_has_no_owner_text(client, admin, manager):
    """Ügyfél behozott gépének címkéjén NINCS tulajdon-felirat; a sajátunkon van."""
    _, adm = admin
    _, mgr = manager
    res = await client.post(
        "/api/assets",
        json={"barcode": "PRN-OWN", "name": "Sajat Gep", "manufacturer": "Jura"},
        headers=mgr,
    )
    own = res.json()
    res = await client.post(
        "/api/assets",
        json={"barcode": "PRN-CUST", "name": "Ugyfel Gepe", "manufacturer": "Saeco",
              "customer_owned": True},
        headers=mgr,
    )
    cust = res.json()
    key = (await client.post("/api/print-jobs/agent-key", headers=adm)).json()["key"]
    agent = {"X-Agent-Key": key}

    await client.post("/api/print-jobs", json={"ids": [own["id"]]}, headers=mgr)
    await client.post("/api/print-jobs", json={"ids": [cust["id"]]}, headers=mgr)
    jobs = (await client.get("/api/print-agent/jobs", headers=agent)).json()["jobs"]
    by_label = {j["label"]: j["payload"] for j in jobs}
    own_payload = next(p for lbl, p in by_label.items() if "PRN-OWN" in lbl)
    cust_payload = next(p for lbl, p in by_label.items() if "PRN-CUST" in lbl)
    assert "tulajdona" in own_payload
    assert "tulajdona" not in cust_payload
    # minden szövegsor a gépnév betűjével (AB-font, 1×) megy ki
    assert ",1,1,0,0,Kod:" in cust_payload
    assert "\nAA," not in cust_payload
