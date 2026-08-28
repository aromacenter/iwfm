"""Publikus arculat-végpont: belépés nélkül elérhető, érzékeny adat nélkül."""

from __future__ import annotations


async def test_branding_public(client, admin):
    _, adm = admin

    # belépés NÉLKÜL is megy — a belépő-oldal fejléce használja
    res = await client.get("/api/settings/branding")
    assert res.status_code == 200, res.text
    out = res.json()
    assert set(out.keys()) == {"company_name", "accent_color", "has_logo"}

    # a cégnév a munkalap-beállításból jön
    ws = (await client.get("/api/settings/worksheet", headers=adm)).json()
    await client.put(
        "/api/settings/worksheet",
        json={**{k: ws[k] for k in (
            "company_name", "company_address", "footer_text",
            "customer_footer_text", "intake_footer_text", "survey_fee",
            "accent_color", "show_materials", "show_hours",
            "show_client_signature", "show_comments",
        )}, "company_name": "Teszt Kávégép Kft."},
        headers=adm,
    )
    res = await client.get("/api/settings/branding")
    assert res.json()["company_name"] == "Teszt Kávégép Kft."

    # logó nélkül a publikus logó-végpont 404
    res = await client.get("/api/settings/branding/logo")
    assert res.status_code == 404
