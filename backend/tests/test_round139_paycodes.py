"""139. kör: bérprogram-formátumú jogcím-CSV — dolgozó × jogcím sorok a havi
nyilvántartásból, beállítható kódok, UTF-8 BOM / CP1250, elválasztó; jog."""

from __future__ import annotations

from tests.test_round117_timesheet import _seed_employee


async def test_paycodes_csv_export(client, admin, manager):
    _, adm = admin
    _, mgr = manager
    await _seed_employee(client, adm, mgr, seed=71, name="Jogcím", site=None)

    res = await client.get("/api/payroll/paycodes.csv?year=2026&month=6", headers=adm)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    text = res.content.decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0] == "adoazonosito;nev;jogcim_kod;jogcim_nev;mennyiseg;egyseg;ev;honap"
    rows = [ln.split(";") for ln in lines[1:]]
    mine = [r for r in rows if r[1] == "Jogcím Teszt"]
    assert mine, rows
    codes = {r[2]: r for r in mine}
    # június 2026: 22 munkanap × 8 óra = 176 keretóra (a nyilvántartott idő blokkolásból jönne)
    assert "KERETORA" in codes and codes["KERETORA"][4] == "176,00"
    assert codes["KERETORA"][5] == "ora" and codes["KERETORA"][6:8] == ["2026", "06"]
    assert len(codes["KERETORA"][0]) == 10  # adóazonosító
    assert "TUL50" not in codes and "TULORA50" not in codes  # nincs túlóra

    # saját jogcím-kódok a beállításban → a CSV azokkal megy; vessző + CP1250
    res = await client.put("/api/settings/work-rules", json={
        "break_minutes": None, "break_per_hours": None, "break_paid": False, "sunday_bonus_default": True,
        "group_by_site": True, "absence_codes": None, "paycodes": {"frame_hours": "K176", "worked_hours": " ", "rm50": "TUL50"},
    }, headers=adm)
    assert res.status_code == 200, res.text
    got = res.json()
    assert got["paycodes"] == {"frame_hours": "K176", "rm50": "TUL50"}
    assert any(d["key"] == "worked_hours" and d["code"] == "MUNKAORA" for d in got["paycodes_defs"])
    res = await client.get("/api/payroll/paycodes.csv?year=2026&month=6&delimiter=,&encoding=cp1250", headers=adm)
    assert res.status_code == 200
    assert "windows-1250" in res.headers["content-type"]
    text = res.content.decode("cp1250")
    assert text.splitlines()[0] == "adoazonosito,nev,jogcim_kod,jogcim_nev,mennyiseg,egyseg,ev,honap"
    # vesszős elválasztónál a "176,00" mennyiség idézőjelbe kerül (CSV-szabály)
    assert ',K176,Havi keretóra,"176,00",ora,2026,06' in text and "KERETORA" not in text

    # rossz paraméter 422
    assert (await client.get("/api/payroll/paycodes.csv?year=2026&month=6&encoding=latin2", headers=adm)).status_code == 422
