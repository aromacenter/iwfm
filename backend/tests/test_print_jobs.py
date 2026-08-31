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


async def test_pdf_print_jobs_and_qr_lookup(client, admin, manager):
    """PDF-nyomtatas a soron at (elismerveny + munkalap) + QR-token felodas."""
    import base64

    _, adm = admin
    _, mgr = manager

    # elismerveny sorba allitasa
    asset = await _make_asset(client, mgr, "PRN-PDF")
    intake = (
        await client.post(
            "/api/intakes",
            json={"asset_id": asset["id"], "client_name": "Nyomtat Elek",
                  "faults": "nem kapcsol be"},
            headers=mgr,
        )
    ).json()
    res = await client.post(f"/api/intakes/{intake['id']}/print", headers=mgr)
    assert res.status_code == 200, res.text

    key = (await client.post("/api/print-jobs/agent-key", headers=adm)).json()["key"]
    agent = {"X-Agent-Key": key}
    jobs = (await client.get("/api/print-agent/jobs", headers=agent)).json()["jobs"]
    pdf_jobs = [j for j in jobs if j["kind"] == "pdf"]
    assert len(pdf_jobs) == 1
    assert intake["serial"] in pdf_jobs[0]["label"]
    raw = base64.b64decode(pdf_jobs[0]["payload"])
    assert raw.startswith(b"%PDF")  # valodi PDF megy az ugynoknek

    # munkalap sorba allitasa (feladat + kitoltetlen ML is nyomtathato? — a
    # PDF-hez munkalap kell, ezert elobb keszitunk egyet a feladathoz)
    from tests.conftest import make_employee_record, make_user

    emp_user, emp_headers = await make_user(email="nyomtato@example.com", role="employee")
    emp = await make_employee_record(emp_user)
    task = (
        await client.post(
            "/api/tasks",
            json={"title": "Nyomtatos feladat", "employee_id": str(emp.id),
                  "due_date": "2026-09-01"},
            headers=mgr,
        )
    ).json()
    ws = await client.put(
        f"/api/me/tasks/{task['id']}/worksheet",
        json={"work_description": "Tisztitas, proba"},
        headers=emp_headers,
    )
    assert ws.status_code == 200, ws.text
    res2 = await client.post(f"/api/me/tasks/{task['id']}/worksheet/print", headers=emp_headers)
    assert res2.status_code == 200, res2.text
    res3 = await client.post(f"/api/tasks/{task['id']}/worksheet/print", headers=mgr)
    assert res3.status_code == 200, res3.text

    jobs2 = (await client.get("/api/print-agent/jobs", headers=agent)).json()["jobs"]
    assert len([j for j in jobs2 if j["kind"] == "pdf"]) == 3

    # QR-token felodas: cimke-sorba tetel general qr_tokent, az by-barcode-dal
    # (szerviz-joggal is) feloldhato
    await client.post("/api/print-jobs", json={"ids": [asset["id"]]}, headers=mgr)
    import app.db as app_db
    from app.models import Asset as AssetModel

    factory = app_db.get_session_factory()
    async with factory() as session:
        row = await session.get(AssetModel, __import__("uuid").UUID(asset["id"]))
        token = row.qr_token
    assert token
    found = await client.get(f"/api/assets/by-barcode/{token}", headers=mgr)
    assert found.status_code == 200
    assert found.json()["id"] == asset["id"]
