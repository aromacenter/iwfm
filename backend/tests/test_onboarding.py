"""Onboarding (2026-10-26): próbaidős licenc (banner-adat + rövid türelmi
idő), ciklus a csomagváltás-kérésben, elfelejtett jelszó, mintaadatok."""
from __future__ import annotations

from datetime import date, timedelta

from tests.test_license import OP, _arm_operator

from app.services.wfm.license import TRIAL_GRACE_DAYS


async def test_trial_license_state_and_short_grace(client, admin, monkeypatch):
    _, adm = admin
    _arm_operator(monkeypatch)
    until = (date.today() + timedelta(days=14)).isoformat()
    res = await client.put(
        "/api/operator/license",
        json={"plan": "xl", "valid_until": until, "is_trial": True, "enabled_modules": ["*"]},
        headers=OP,
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["is_trial"] is True and out["days_left"] == 14 and out["grace_days"] == TRIAL_GRACE_DAYS
    # lejárt próba: 3 nap türelmi, utána expired
    past = (date.today() - timedelta(days=2)).isoformat()
    out = (await client.put("/api/operator/license", json={"plan": "xl", "valid_until": past}, headers=OP)).json()
    assert out["is_trial"] is True and out["state"] == "grace" and out["grace_days_left"] == TRIAL_GRACE_DAYS - 2
    past = (date.today() - timedelta(days=TRIAL_GRACE_DAYS + 1)).isoformat()
    out = (await client.put("/api/operator/license", json={"plan": "xl", "valid_until": past}, headers=OP)).json()
    assert out["state"] == "expired"
    # fizetés után a Flotta leveszi a trial-jelzőt → normál türelmi idő
    out = (await client.put("/api/operator/license", json={"plan": "m", "valid_until": until, "is_trial": False}, headers=OP)).json()
    assert out["is_trial"] is False and out["grace_days"] == 14
    status = (await client.get("/api/settings/license/status", headers=adm)).json()
    assert status["is_trial"] is False


async def test_plan_request_carries_cycle(client, admin, monkeypatch):
    _, adm = admin
    _arm_operator(monkeypatch)
    await client.put("/api/operator/license", json={"plan": "s", "valid_until": None}, headers=OP)
    res = await client.post("/api/settings/license/request", json={"plan": "m", "cycle": "yearly"}, headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["requested_plan"] == "m" and res.json()["requested_cycle"] == "yearly"
    st = (await client.get("/api/operator/status", headers=OP)).json()
    assert st["requested_cycle"] == "yearly"
    # az üzemeltetői mentés lezárja a kérést a ciklussal együtt
    out = (await client.put("/api/operator/license", json={"plan": "m", "valid_until": None}, headers=OP)).json()
    assert out["requested_plan"] is None and out["requested_cycle"] is None


async def test_forgot_password_sends_link(client, admin, monkeypatch):
    _, adm = admin
    from app.api import auth as auth_mod
    from app.services.wfm import email_service

    me = (await client.get("/api/auth/me", headers=adm)).json()
    sent: list[tuple[str, str]] = []

    async def fake_send(config, to, subject, body, attachments=None):
        sent.append((to, body))
        return True

    monkeypatch.setattr(email_service, "send_email", fake_send)
    # SMTP nélkül: 200, de jelzi, hogy nem ment levél
    res = await client.post("/api/auth/forgot", json={"email": me["email"]})
    assert res.status_code == 200 and res.json().get("mail_unavailable") is True
    # platform-SMTP env-tartalékkal megy a link
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(get_settings(), "smtp_from", "noreply@example.com", raising=False)
    res = await client.post("/api/auth/forgot", json={"email": me["email"].upper()})
    assert res.status_code == 200 and sent and sent[-1][0] == me["email"] and "/jelszo?token=" in sent[-1][1]
    # ismeretlen cím: ugyanúgy 200, levél nélkül
    n = len(sent)
    res = await client.post("/api/auth/forgot", json={"email": "senki@example.com"})
    assert res.status_code == 200 and len(sent) == n


async def test_demo_data_create_and_delete(client, admin):
    _, adm = admin
    res = await client.post("/api/settings/demo-data", headers=adm)
    assert res.status_code == 200, res.text
    assert res.json()["partners"] == 3 and res.json()["assets"] == 3
    # idempotens
    res = await client.post("/api/settings/demo-data", headers=adm)
    assert res.json()["partners"] == 0 and res.json()["assets"] == 0
    partners = (await client.get("/api/partners", headers=adm)).json()
    assert sum(1 for p in partners if p["name"].startswith("[MINTA]")) == 3
    res = await client.delete("/api/settings/demo-data", headers=adm)
    assert res.status_code == 200 and res.json()["partners"] == 3
    partners = (await client.get("/api/partners", headers=adm)).json()
    assert not any(p["name"].startswith("[MINTA]") for p in partners)
