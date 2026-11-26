"""184. kör: áremelés / indexálás — előnézet, ütemezés, alkalmazás, visszavonás,
értesítő levél partnerenkénti táblával, hatályba lépéskor automatikus futás."""

from __future__ import annotations

from datetime import date, timedelta

from tests.test_consignment import make_product
from tests.test_inventory import make_partner


def test_new_price_rounding():
    from app.services.wfm.price_indexation import new_price

    assert new_price(100, 10, None, 1) == 110
    assert new_price(97, 10, None, 10) == 110  # 106.7 → felfelé tízesre
    assert new_price(100, None, 15, 1) == 115
    assert new_price(100, 10, 5, 100) == 200  # 115 → százasra
    assert new_price(52.4, 5, None, 1) == 55.02


async def _setup(client, adm, name="Indexált Kávézó"):
    partner = await make_partner(client, adm, name=name)
    product = await make_product(client, adm, name=f"Kávé {name}")
    res = await client.put(f"/api/partners/{partner['id']}/prices/{product['id']}", json={"price_per_portion": 60}, headers=adm)
    assert res.status_code == 200, res.text
    res = await client.post(f"/api/partners/{partner['id']}/contracts", json={
        "valid_from": "2026-01-01", "min_portions": 500, "below_min_price": 40,
        "price_tiers": [{"qty_from": 0, "qty_to": 1000, "price": 50}, {"qty_from": 1001, "qty_to": None, "price": 45}],
        "fixed_rental": False, "payment_method": "transfer", "payment_terms_days": 8,
    }, headers=adm)
    assert res.status_code == 201, res.text
    return partner, product, res.json()


async def test_preview_schedule_apply_revert(client, admin):
    _, adm = admin
    partner, product, contract = await _setup(client, adm)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    body = {"partner_ids": [partner["id"]], "percent": 10, "round_to": 1, "effective_from": tomorrow,
            "targets": ["partner_price", "below_min", "tier", "rent_fee"]}
    res = await client.post("/api/price-indexations/preview", json=body, headers=adm)
    assert res.status_code == 200, res.text
    pv = res.json()
    kinds = sorted(r["kind"] for r in pv["rows"])
    assert kinds == ["below_min", "partner_price", "tier", "tier"]
    assert pv["partners"] == 1 and pv["items"] == 4
    assert next(r for r in pv["rows"] if r["kind"] == "partner_price")["new"] == 66

    res = await client.post("/api/price-indexations", json={**body, "name": "2027 indexálás"}, headers=adm)
    assert res.status_code == 201, res.text
    ix = res.json()
    assert ix["status"] == "scheduled" and ix["items"] == 4
    # még nem változott semmi
    prices = (await client.get(f"/api/partners/{partner['id']}/prices", headers=adm)).json()
    assert next(p for p in prices if p["product_id"] == product["id"])["price_per_portion"] == 60

    res = await client.post(f"/api/price-indexations/{ix['id']}/apply", headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "applied"
    prices = (await client.get(f"/api/partners/{partner['id']}/prices", headers=adm)).json()
    assert next(p for p in prices if p["product_id"] == product["id"])["price_per_portion"] == 66
    cs = (await client.get(f"/api/partners/{partner['id']}/contracts", headers=adm)).json()
    c = next(x for x in cs if x["id"] == contract["id"])
    assert c["below_min_price"] == 44 and [t["price"] for t in c["price_tiers"]] == [55, 49.5]
    # a partner tükre is frissült
    from sqlalchemy import select

    import app.db as app_db
    from app.models import Partner

    async with app_db.get_session_factory()() as db:
        row = (await db.execute(select(Partner).where(Partner.name == "Indexált Kávézó"))).scalar_one()
        assert row.contract_below_min_price == 44

    res = await client.post(f"/api/price-indexations/{ix['id']}/revert", headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "reverted"
    prices = (await client.get(f"/api/partners/{partner['id']}/prices", headers=adm)).json()
    assert next(p for p in prices if p["product_id"] == product["id"])["price_per_portion"] == 60
    cs = (await client.get(f"/api/partners/{partner['id']}/contracts", headers=adm)).json()
    assert next(x for x in cs if x["id"] == contract["id"])["below_min_price"] == 40
    # a napló
    log = (await client.get("/api/audit?limit=20", headers=adm)).json()
    actions = {e["action"] for e in (log if isinstance(log, list) else log.get("items", []))}
    assert {"price.indexation_scheduled", "price.indexation_applied", "price.indexation_reverted"} <= actions


async def test_effective_today_applies_immediately_and_scheduled_runs_hourly(client, admin):
    import app.db as app_db
    from app.services.wfm.price_indexation import run_scheduled_indexations

    _, adm = admin
    partner, product, _ = await _setup(client, adm, name="Azonnali Bisztró")
    res = await client.post("/api/price-indexations", json={
        "name": "most", "partner_ids": [partner["id"]], "amount": 5, "round_to": 1,
        "effective_from": date.today().isoformat(), "targets": ["partner_price"],
    }, headers=adm)
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "applied"
    prices = (await client.get(f"/api/partners/{partner['id']}/prices", headers=adm)).json()
    assert next(p for p in prices if p["product_id"] == product["id"])["price_per_portion"] == 65

    # ütemezett, hatály holnap → az óránkénti kör ma nem, holnap igen
    res = await client.post("/api/price-indexations", json={
        "name": "holnap", "partner_ids": [partner["id"]], "percent": 20, "round_to": 10,
        "effective_from": (date.today() + timedelta(days=1)).isoformat(), "targets": ["partner_price"],
    }, headers=adm)
    assert res.status_code == 201 and res.json()["status"] == "scheduled"
    async with app_db.get_session_factory()() as db:
        assert await run_scheduled_indexations(db, date.today()) == 0
        assert await run_scheduled_indexations(db, date.today() + timedelta(days=1)) == 1
    prices = (await client.get(f"/api/partners/{partner['id']}/prices", headers=adm)).json()
    assert next(p for p in prices if p["product_id"] == product["id"])["price_per_portion"] == 80  # 78 → 80
    # semmi változtatnivaló → 422
    res = await client.post("/api/price-indexations", json={
        "name": "üres", "partner_ids": [partner["id"]], "percent": 10, "effective_from": date.today().isoformat(),
        "targets": ["rent_fee"],
    }, headers=adm)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "price.nothing_to_change"


async def test_notice_letters_per_partner(client, admin, monkeypatch):
    import app.api.price_indexation as api_mod
    from app.services.wfm import email_service

    _, adm = admin
    partner, _, _ = await _setup(client, adm, name="Levelezős Kft.")
    res = await client.post("/api/price-indexations", json={
        "name": "levél", "partner_ids": [partner["id"]], "percent": 10, "round_to": 1,
        "effective_from": (date.today() + timedelta(days=30)).isoformat(), "targets": ["partner_price", "below_min"],
    }, headers=adm)
    assert res.status_code == 201, res.text
    ix = res.json()
    cfg = email_service.SmtpConfig(host="smtp.example", port=25, username=None, password=None, from_address="x@example.com", use_tls=False)

    async def fake_load(db):
        return cfg

    sent: list[tuple[str, str, str]] = []

    async def fake_send(config, to, subject, body, attachments=None, html=None):
        sent.append((to, subject, body))
        return True

    monkeypatch.setattr(api_mod.email_service, "load_smtp_config", fake_load)
    monkeypatch.setattr(email_service, "send_email", fake_send)
    body = {"subject": "Árváltozás {{hataly}}", "body": "Tisztelt {{partner_nev}}!\n\n{{mertek}} emelés {{hataly}} napjától:\n{{tabla}}"}
    res = await client.post(f"/api/price-indexations/{ix['id']}/notice-preview", json=body, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["partner"] == "Levelezős Kft." and "60 Ft → 66 Ft" in res.json()["body"]
    res = await client.post(f"/api/price-indexations/{ix['id']}/notify", json=body, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["recipients"] == 1
    assert sent and sent[0][0] == "p@example.com" and "Levelezős Kft." in sent[0][2] and "10% emelés" in sent[0][2]
    assert "40 Ft → 44 Ft" in sent[0][2]
    camps = (await client.get("/api/mailing/campaigns", headers=adm)).json()
    assert camps and camps[0]["recipient_count"] == 1
    one = (await client.get(f"/api/price-indexations/{ix['id']}", headers=adm)).json()
    assert one["notice_sent_at"] is not None
    # nem admin: 403
    from tests.conftest import make_user

    _, mgr = await make_user(email="mgr184@example.com", role="manager")
    assert (await client.get("/api/price-indexations", headers=mgr)).status_code == 403
