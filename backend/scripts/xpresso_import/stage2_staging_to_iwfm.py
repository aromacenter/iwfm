# -*- coding: utf-8 -*-
"""2. lépcső: staging SQLite → Iwfm DB (partnerek, termékek, gépek,
szerviz-előzmények, elszámolások 5 évre, partner-készletek, partner-árak).

Cél DB: IMPORT_DB_URL környezeti változó (sqlite+aiosqlite:// vagy
postgresql+asyncpg://). Kétszeri futtatás ellen véd: ha már van
"Xpresso-ID" jelölésű partner, csak IMPORT_FORCE=1 esetén fut le újra.

Árkezelés: a régi rendszer elszámolás-összegeit BRUTTÓnak vesszük (a vevő
ennyit fizetett) — nettó = összeg / 1.27; az eredeti adagár a megjegyzésben
megmarad.
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import sqlite3
import statistics
import sys
import uuid
from datetime import UTC, date, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\palvo\Desktop\appok shopify\Iwfm\backend")

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.models import (  # noqa: E402
    Asset,
    AssetMovement,
    AuditEvent,
    Partner,
    PartnerPrice,
    PartnerStock,
    Product,
    ServiceTicket,
    Settlement,
    SettlementLine,
    StockMovement,
)

STAGING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xpresso_staging.db")
CUTOFF = "2021-08-09"
VAT = 1.27
BATCH = 800

FAJTA_NEV = {
    11: "Kávégép", 12: "Több számlálós kávégép", 13: "Kávégép számláló",
    14: "Kávédaráló", 15: "Vízlágyító",
}
HELY_NEV = {0: "Polc", 1: "Szerviz", 2: "Partner", 3: "Külső raktár",
            4: "Ügyfél", 5: "Ügyfél (cseregép)"}


def dt(s: str | None) -> datetime | None:
    if not s or str(s).startswith("0000"):
        return None
    s = str(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def d(s: str | None) -> date | None:
    v = dt(s)
    return v.date() if v else None


def clean(s, limit: int | None = None) -> str | None:
    if s is None:
        return None
    out = str(s).strip()
    if not out or out.upper() == "NULL":
        return None
    return out[:limit] if limit else out


def money(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


async def main() -> None:
    url = os.environ["IMPORT_DB_URL"]
    force = os.environ.get("IMPORT_FORCE") == "1"
    engine = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    st = sqlite3.connect(STAGING)
    st.row_factory = sqlite3.Row
    report: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        report.append(msg)

    async with Session() as db:
        # ---- védelem kétszeri futtatás ellen ----
        marked = (
            await db.execute(
                select(func.count()).select_from(Partner).where(Partner.notes.like("%Xpresso-ID%"))
            )
        ).scalar_one()
        if marked and not force:
            print(f"MÁR IMPORTÁLVA ({marked} partner jelölt) — IMPORT_FORCE=1 kell az újrafuttatáshoz.")
            return

        # ---- meglévő állapot a cél-DB-ben ----
        existing_partner_names = {
            (n or "").strip().lower()
            for n in (await db.execute(select(Partner.name))).scalars()
        }
        existing_codes = list((await db.execute(select(Partner.partner_code))).scalars())
        nums = [int(c[3:]) for c in existing_codes if c and c.startswith("PT-") and c[3:].isdigit()]
        next_pt = (max(nums) + 1) if nums else 1
        existing_barcodes = {
            (b or "").strip() for b in (await db.execute(select(Asset.barcode))).scalars()
        }
        existing_product_names = {
            (n or "").strip().lower()
            for n in (await db.execute(select(Product.name))).scalars()
        }
        last_ticket = (
            await db.execute(
                select(ServiceTicket.ticket_no)
                .where(ServiceTicket.ticket_no.like("SZ-%"))
                .order_by(ServiceTicket.ticket_no.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        next_sz = 1
        if last_ticket:
            try:
                next_sz = int(last_ticket.split("-", 1)[1]) + 1
            except (IndexError, ValueError):
                pass

        # ---- staging lookupok ----
        users = {r["user_id"]: clean(r["nev"], 256) or clean(r["username"], 256) or "Ismeretlen"
                 for r in st.execute("SELECT user_id, nev, username FROM User_LISTA")}
        gyartok = {r["eszkoz_gyarto_id"]: clean(r["gyarto_nev"], 128)
                   for r in st.execute("SELECT * FROM Term_GYARTOK")}
        tipusok = {
            r["eszkoz_tipus_id"]: (
                gyartok.get(r["eszkoz_gyarto_id"]),
                clean(r["eszkoz_tipus_nev"], 200),
                clean(r["eszkoz_cikkszam"], 64),
            )
            for r in st.execute("SELECT * FROM Term_GYARTOK_TIPUSOK")
        }
        # Elszamolas_TERMEKEK névfeloldás: term_id → legutóbbi név a mozgásokból
        term_nev: dict[int, str] = {}
        for r in st.execute(
            "SELECT term_id, nev FROM Mozgas_RESZLETES WHERE nev IS NOT NULL ORDER BY MozgReszlID"
        ):
            term_nev[r["term_id"]] = clean(r["nev"], 250) or f"Termék #{r['term_id']}"
        # aktív szerződések vonalkód szerint
        contracts: dict[str, sqlite3.Row] = {}
        for r in st.execute(
            "SELECT * FROM Partner_ELSZAM_SZERZODESEK WHERE aktiv=1 ORDER BY datum_tol"
        ):
            vk = clean(r["vonalkod"], 64)
            if vk:
                contracts[vk] = r

        # =========================================================
        # 1) PARTNEREK
        # =========================================================
        # Csak az import ELŐTT létező partnerekhez illesztünk névre (demo/teszt
        # sorok) — a dumpon belüli névazonos partnerek külön partnerek maradnak.
        partner_map: dict[int, uuid.UUID] = {}
        skipped_partners = 0
        new_partners = 0
        for r in st.execute("SELECT * FROM Partner_LISTA ORDER BY partner_id"):
            name = clean(r["uzlet_neve"], 256) or clean(r["cegnev"], 256)
            if not name:
                skipped_partners += 1
                continue
            key = name.lower()
            if key in existing_partner_names:
                # névazonos meglévő partner → arra képezzük az öreg ID-t
                pid = (
                    await db.execute(select(Partner.id).where(func.lower(Partner.name) == key))
                ).scalars().first()
                if pid:
                    partner_map[r["partner_id"]] = pid
                skipped_partners += 1
                continue
            notes = [f"Xpresso-ID: {r['partner_id']}"]
            fajta = "Partner (elszámolós)" if r["partner_fajta"] == 1 else "Ügyfél"
            notes.append(f"Xpresso-típus: {fajta}")
            if clean(r["cegnev"]) and clean(r["cegnev"]) != name:
                notes.append(f"Cégnév: {clean(r['cegnev'])}")
            if money(r["tartozas"]):
                notes.append(f"Tartozás az átálláskor: {money(r['tartozas']):.0f} Ft")
            if clean(r["megjegyzes"]):
                notes.append(f"Megjegyzés: {clean(r['megjegyzes'])}")
            p = Partner(
                id=uuid.uuid4(),
                partner_code=f"PT-{next_pt:04d}",
                name=name,
                partner_type="customer",
                tax_number=clean(r["adoszam"], 32),
                contact_name=clean(r["kapcsolat_nev"], 256),
                contact_email=clean(r["email"], 320),
                contact_phone=clean(r["telefonszam"], 32),
                address_zip=clean(r["uzlet_cime_isz"], 16),
                address_city=clean(r["uzlet_cime_varos"], 128),
                address_street=clean(r["uzlet_cime_utca"], 256),
                address=", ".join(
                    x for x in (
                        clean(r["uzlet_cime_isz"]), clean(r["uzlet_cime_varos"]),
                        clean(r["uzlet_cime_utca"]),
                    ) if x
                )[:512] or None,
                billing_address=clean(r["szekhely"], 512),
                notes="\n".join(notes),
                is_active=bool(r["aktiv"]),
            )
            next_pt += 1
            db.add(p)
            partner_map[r["partner_id"]] = p.id
            new_partners += 1
            if new_partners % BATCH == 0:
                await db.flush()
        await db.commit()
        log(f"Partnerek: {new_partners} új, {skipped_partners} kihagyva (névazonos/üres)")

        # =========================================================
        # 2) TERMÉKEK — kávék + egyéb (Term_KV_KAVE), alkatrészek
        # =========================================================
        product_map: dict[int, uuid.UUID] = {}  # kv_id → product uuid
        product_name_by_kv: dict[int, str] = {}
        new_products = 0
        for r in st.execute("SELECT * FROM Term_KV_KAVE ORDER BY kv_id"):
            name = clean(r["nev"], 256)
            if not name:
                continue
            if name.lower() in existing_product_names:
                name = f"{name} (X{r['kod']})"[:256]
            deleted = bool(r["torolve"]) if r["torolve"] not in (None, "") else False
            notes = [f"Xpresso kód: {r['kod']}"]
            if clean(r["szemes_orolt"]):
                notes.append(f"Fajta: {clean(r['szemes_orolt'])}")
            if money(r["ar_elad"]):
                notes.append(f"Régi eladási ár (nettó/egység): {money(r['ar_elad']):.2f} Ft")
            pr = Product(
                id=uuid.uuid4(),
                name=name,
                unit="kg",
                grams_per_portion=7,
                price_per_portion=0.0,  # később: partner-árak mediánja
                vat_percent=27,
                is_active=not deleted,
                notes="\n".join(notes),
            )
            db.add(pr)
            product_map[r["kv_id"]] = pr.id
            product_name_by_kv[r["kv_id"]] = name
            existing_product_names.add(name.lower())
            new_products += 1
        await db.commit()
        log(f"Termékek (kávé/egyéb): {new_products}")

        new_parts = 0
        for r in st.execute("SELECT * FROM Term_ALK_CIKKSZ ORDER BY kod"):
            name = clean(r["nev"], 240)
            if not name:
                continue
            display = name
            if display.lower() in existing_product_names:
                display = f"{name} ({clean(r['kod']) or r['eszkoz_id']})"[:256]
            notes = ["Alkatrész (Xpresso import)"]
            if clean(r["kod"]):
                notes.append(f"Kód: {clean(r['kod'])}")
            if clean(r["cikkszam"]):
                notes.append(f"Cikkszám: {clean(r['cikkszam'])}")
            pr = Product(
                id=uuid.uuid4(),
                name=display,
                unit="db",
                grams_per_portion=1,
                price_per_portion=money(r["elad_ar"]),
                vat_percent=27,
                low_stock_threshold=float(r["min_darab"]) if r["min_darab"] else None,
                is_active=False,  # ne zavarja a bizományos listákat / QR-rendelést
                notes="\n".join(notes),
            )
            db.add(pr)
            existing_product_names.add(display.lower())
            new_parts += 1
            if new_parts % BATCH == 0:
                await db.flush()
        await db.commit()
        log(f"Termékek (alkatrész, inaktívként): {new_parts}")

        # =========================================================
        # 3) GÉPEK (Term_ESZ_VK) + mozgások
        # =========================================================
        asset_map: dict[str, uuid.UUID] = {}  # vonalkód → uuid
        new_assets = skipped_assets = 0
        for r in st.execute("SELECT * FROM Term_ESZ_VK ORDER BY vonalkod"):
            barcode = clean(r["vonalkod"], 64)
            if not barcode or barcode in existing_barcodes:
                skipped_assets += 1
                continue
            manu, tip_nev, tip_cikk = tipusok.get(r["tipus_id"], (None, None, None))
            name = tip_nev or clean(r["cikkszam"], 128) or FAJTA_NEV.get(r["fajta_id"], "Gép")
            hely_fajta = r["hely_fajta_id"]
            partner_id = None
            status = "in_stock"
            deployed_at = None
            location_type = HELY_NEV.get(hely_fajta)
            if hely_fajta in (2, 4, 5):
                partner_id = partner_map.get(r["hely_id"])
                if partner_id:
                    status = "deployed"
                else:
                    location_type = "Polc"  # ismeretlen partner → raktár
            elif hely_fajta == 1:
                status = "maintenance"
            notes = [f"Xpresso hely: {HELY_NEV.get(hely_fajta, hely_fajta)}"]
            ctr = contracts.get(barcode)
            if ctr is not None:
                deployed_at = dt(ctr["datum_tol"])
                terms = []
                if money(ctr["adag_ar_ft"]):
                    terms.append(f"adagár {money(ctr['adag_ar_ft']):.0f} Ft")
                if money(ctr["adag_ar_akcio_ft"]):
                    terms.append(f"akciós adagár {money(ctr['adag_ar_akcio_ft']):.0f} Ft")
                if ctr["minimum_adag_db"]:
                    terms.append(f"min. {ctr['minimum_adag_db']} adag")
                if money(ctr["adag_ar_miminum_alatt_ft"]):
                    terms.append(f"min. alatti adagár {money(ctr['adag_ar_miminum_alatt_ft']):.0f} Ft")
                if money(ctr["berleti_dij_osszege_ft"]):
                    terms.append(f"bérleti díj {money(ctr['berleti_dij_osszege_ft']):.0f} Ft/hó")
                if terms:
                    notes.append("Szerződés (import): " + ", ".join(terms))
                if clean(ctr["datum_tol"]):
                    notes.append(f"Szerződés kezdete: {clean(ctr['datum_tol'])[:10]}")
            if clean(r["utolso_vizkotelenites"]) and not str(r["utolso_vizkotelenites"]).startswith("0000"):
                notes.append(f"Utolsó vízkőtelenítés: {clean(r['utolso_vizkotelenites'])[:10]}")
            if status == "deployed" and deployed_at is None:
                deployed_at = datetime.now(UTC)
            a = Asset(
                id=uuid.uuid4(),
                barcode=barcode,
                name=name[:256],
                category=FAJTA_NEV.get(r["fajta_id"]),
                manufacturer=manu,
                article_number=clean(r["cikkszam"], 64) or tip_cikk,
                serial_number=clean(r["gyart_szam"], 128),
                location_type=location_type,
                counter=int(r["szamlalo"]) if r["szamlalo"] not in (None, "") else None,
                norm=float(r["norma"]) if r["norma"] not in (None, "") else None,
                tangible=bool(r["targyi"]),
                status=status,
                partner_id=partner_id,
                deployed_at=deployed_at,
                notes="\n".join(notes),
            )
            db.add(a)
            db.add(AssetMovement(asset_id=a.id, action="created", detail="Import a régi (Xpresso) rendszerből"))
            if status == "deployed" and partner_id:
                db.add(AssetMovement(
                    asset_id=a.id, action="deploy", partner_id=partner_id,
                    detail="Kihelyezés (Xpresso import)",
                ))
            asset_map[barcode] = a.id
            existing_barcodes.add(barcode)
            new_assets += 1
            if new_assets % BATCH == 0:
                await db.flush()
        await db.commit()
        log(f"Gépek: {new_assets} új, {skipped_assets} kihagyva (meglévő/üres vonalkód)")

        # partner_gep_id → (vonalkód, partner_id) a szerződésekből (elszámolás-sorokhoz)
        gep_info: dict[int, tuple[str | None, int | None]] = {}
        for r in st.execute("SELECT partner_gep_id, vonalkod, partner_id FROM Partner_ELSZAM_SZERZODESEK"):
            gep_info[r["partner_gep_id"]] = (clean(r["vonalkod"], 64), r["partner_id"])

        # =========================================================
        # 4) SZERVIZ-ELŐZMÉNYEK (Szerviz_MUNKALAP — mind)
        # =========================================================
        old_partner_names = {
            r["partner_id"]: clean(r["uzlet_neve"], 256)
            for r in st.execute("SELECT partner_id, uzlet_neve FROM Partner_LISTA")
        }
        munkak: dict[int, list[str]] = {}
        for r in st.execute("SELECT szerviz_id, megnevezes, munkadij FROM Szerviz_MUNKA"):
            if clean(r["megnevezes"]):
                munkak.setdefault(r["szerviz_id"], []).append(
                    f"{clean(r['megnevezes'])} ({money(r['munkadij']):.0f} Ft)"
                )
        new_tickets = 0
        recent_open_limit = datetime(2026, 1, 1, tzinfo=UTC)
        for r in st.execute("SELECT * FROM Szerviz_MUNKALAP ORDER BY felvetel_datum, szerviz_id"):
            created = dt(r["felvetel_datum"]) or datetime.now(UTC)
            finished = dt(r["szerviz_veg_datum"]) or dt(r["kiadas_datum"])
            if finished or r["allapot_id"] == 2:
                status = "done"
                resolved = finished or created
            elif r["stadium_id"] == 2:
                status, resolved = "cancelled", None
            elif created >= recent_open_limit:
                status, resolved = "open", None
            else:
                status, resolved = "cancelled", None  # elavult függő tétel
            hiba = clean(r["hiba"]) or "Szervizmunka"
            desc_parts = [hiba]
            if clean(r["megjegyzes_belso"]):
                desc_parts.append(f"Belső megjegyzés: {clean(r['megjegyzes_belso'])}")
            if munkak.get(r["szerviz_id"]):
                desc_parts.append("Elvégzett munkák: " + "; ".join(munkak[r["szerviz_id"]][:12]))
            if status == "cancelled" and not finished and r["stadium_id"] != 2:
                desc_parts.append("Importkor automatikusan lezárva (régi, függőben maradt tétel).")
            vk = clean(r["eszkoz_vonalkod"], 64)
            asset_id = asset_map.get(vk) if vk else None
            partner_id = partner_map.get(r["partner_id"])
            partner_name = old_partner_names.get(r["partner_id"])
            t = ServiceTicket(
                id=uuid.uuid4(),
                ticket_no=f"SZ-{next_sz:04d}",
                kind="repair",
                status=status,
                priority="normal",
                title=(hiba[:250] + "…") if len(hiba) > 250 else hiba[:256],
                description="\n".join(desc_parts) + f"\nXpresso munkalap: #{r['szerviz_id']}",
                asset_id=asset_id,
                asset_label=vk,
                partner_id=partner_id,
                partner_label=partner_name,
                assigned_to_name=users.get(r["dolgozo_user_id"]),
                counter_at_service=int(r["kvg_szamlalo"]) if r["kvg_szamlalo"] not in (None, "") else None,
                resolved_at=resolved,
                created_at=created,
            )
            db.add(t)
            next_sz += 1
            new_tickets += 1
            if new_tickets % BATCH == 0:
                await db.flush()
        await db.commit()
        log(f"Szervizjegyek: {new_tickets} (SZ-{next_sz - new_tickets:04d}…SZ-{next_sz - 1:04d})")

        # =========================================================
        # 5) ELSZÁMOLÁSOK (5 év, befejezett) + tételek
        # =========================================================
        gep_sorok: dict[int, list[sqlite3.Row]] = {}
        for r in st.execute(
            "SELECT g.* FROM Elszamolas_KAVEGEP g JOIN Elszamolas e ON e.elszamolas_id=g.elszamolas_id "
            "WHERE e.befejezve=1 AND e.elszamolas_end >= ?", (CUTOFF,)
        ):
            gep_sorok.setdefault(r["elszamolas_id"], []).append(r)
        term_sorok: dict[int, list[sqlite3.Row]] = {}
        for r in st.execute(
            "SELECT t.* FROM Elszamolas_TERMEKEK t JOIN Elszamolas e ON e.elszamolas_id=t.elszamolas_id "
            "WHERE e.befejezve=1 AND e.elszamolas_end >= ?", (CUTOFF,)
        ):
            term_sorok.setdefault(r["elszamolas_id"], []).append(r)

        new_settl = new_lines = skipped_settl = 0
        for r in st.execute(
            "SELECT * FROM Elszamolas WHERE befejezve=1 AND elszamolas_end >= ? "
            "ORDER BY elszamolas_end, elszamolas_id", (CUTOFF,)
        ):
            partner_id = partner_map.get(r["partner_id"])
            created = dt(r["elszamolas_end"]) or dt(r["elszamolas_start"])
            if not partner_id or not created:
                skipped_settl += 1
                continue
            total_gross = money(r["osszes_fizetendo"])
            fizetve = money(r["fizetve"])
            tartozas = money(r["tartozas"])
            notes = [f"Xpresso elszámolás: #{r['elszamolas_id']}"]
            if r["kv_id"] in product_name_by_kv:
                notes.append(f"Kávé: {product_name_by_kv[r['kv_id']]}")
            adatok = []
            if r["atadott_menny"] not in (None, ""):
                adatok.append(f"átadott {money(r['atadott_menny']):g} kg")
            if r["keszlet"] not in (None, ""):
                adatok.append(f"készlet {money(r['keszlet']):g} kg")
            if r["hiany"] not in (None, ""):
                adatok.append(f"fogyás/hiány {money(r['hiany']):g} kg")
            if adatok:
                notes.append("Leltár: " + ", ".join(adatok))
            if abs(fizetve - total_gross) > 2:
                notes.append(f"Fizetve: {fizetve:.0f} Ft")
            if tartozas > 2:
                notes.append(f"TARTOZÁS: {tartozas:.0f} Ft")
            s = Settlement(
                id=uuid.uuid4(),
                partner_id=partner_id,
                settled_by_name=users.get(r["user_id"], "Ismeretlen"),
                payment_method="cash",
                total_net=round(total_gross / VAT, 2),
                total_gross=total_gross,
                invoiced=False,
                payment_status="none",
                note="\n".join(notes),
                created_at=created,
            )
            db.add(s)
            new_settl += 1
            for g in gep_sorok.get(r["elszamolas_id"], []):
                if g["kihagyva"]:
                    continue
                vk, _gp = gep_info.get(g["partner_gep_id"], (None, None))
                adag = money(g["lefozott_adag"])
                osszeg = money(g["fizetendo_osszeg"])
                adag_ar = money(g["adag_ar_kedvezmenyes"]) or money(g["adag_ar_eredeti"])
                if not adag and not osszeg and not money(g["berleti_dij"]):
                    continue
                label = f"Adagelszámolás — gép {vk}" if vk else "Adagelszámolás"
                if osszeg or adag:
                    db.add(SettlementLine(
                        settlement_id=s.id,
                        product_id=product_map.get(r["kv_id"]),
                        product_name=label[:256],
                        previous_qty=0.0, physical_qty=0.0, consumed_qty=0.0,
                        portions=adag,
                        price_per_portion=round(adag_ar / VAT, 2),
                        vat_percent=27,
                        amount_net=round((osszeg or adag * adag_ar) / VAT, 2),
                    ))
                    new_lines += 1
                if money(g["berleti_dij"]):
                    db.add(SettlementLine(
                        settlement_id=s.id,
                        product_name=(f"Bérleti díj — gép {vk}" if vk else "Bérleti díj")[:256],
                        previous_qty=0.0, physical_qty=0.0, consumed_qty=0.0,
                        portions=1.0,
                        price_per_portion=round(money(g["berleti_dij"]) / VAT, 2),
                        vat_percent=27,
                        amount_net=round(money(g["berleti_dij"]) / VAT, 2),
                    ))
                    new_lines += 1
            for t in term_sorok.get(r["elszamolas_id"], []):
                darab = money(t["darab"])
                elad_ar = money(t["elad_ar"])  # a régi rendszerben nettó
                if not darab:
                    continue
                kedv = money(t["kedvezmeny"])
                net = round(darab * elad_ar * (1 - kedv / 100.0), 2)
                db.add(SettlementLine(
                    settlement_id=s.id,
                    product_name=term_nev.get(t["termek_id"], f"Termék #{t['termek_id']}"),
                    previous_qty=0.0, physical_qty=0.0,
                    consumed_qty=darab, portions=darab,
                    price_per_portion=elad_ar,
                    vat_percent=27,
                    amount_net=net,
                ))
                new_lines += 1
            if new_settl % 400 == 0:
                await db.flush()
                await db.commit()
        await db.commit()
        log(f"Elszámolások: {new_settl} ({skipped_settl} kihagyva), tétel: {new_lines}")

        # =========================================================
        # 6) PARTNER-KÉSZLET (utolsó elszámolás készlete) + PARTNER-ÁRAK
        # =========================================================
        new_stock = 0
        partner_last_kv: dict[int, int] = {}
        for r in st.execute(
            "SELECT partner_id, kv_id, keszlet FROM Elszamolas WHERE befejezve=1 "
            "ORDER BY elszamolas_end"
        ):
            partner_last_kv[r["partner_id"]] = r["kv_id"]
        stock_seen: set[tuple[uuid.UUID, uuid.UUID]] = {
            (row.partner_id, row.product_id)
            for row in (
                await db.execute(select(PartnerStock.partner_id, PartnerStock.product_id))
            ).all()
        }
        for r in st.execute(
            "SELECT e.partner_id, e.kv_id, e.keszlet FROM Elszamolas e JOIN ("
            " SELECT partner_id, MAX(elszamolas_end) me FROM Elszamolas WHERE befejezve=1 GROUP BY partner_id"
            ") m ON m.partner_id=e.partner_id AND m.me=e.elszamolas_end WHERE e.befejezve=1"
        ):
            qty = money(r["keszlet"])
            pid = partner_map.get(r["partner_id"])
            prod = product_map.get(r["kv_id"])
            if not pid or not prod or qty <= 0 or (pid, prod) in stock_seen:
                continue
            stock_seen.add((pid, prod))
            db.add(PartnerStock(partner_id=pid, product_id=prod, quantity=qty))
            db.add(StockMovement(
                partner_id=pid, product_id=prod, action="replenish",
                quantity_delta=qty, note="Xpresso nyitókészlet (import)",
            ))
            new_stock += 1
        await db.commit()
        log(f"Partner-készletek: {new_stock}")

        # partner-árak: aktív szerződés adagára (bruttó → nettó) a partner kávéjára
        price_rows: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
        prices_by_product: dict[uuid.UUID, list[float]] = {}
        for vk, ctr in contracts.items():
            old_pid = ctr["partner_id"]
            pid = partner_map.get(old_pid)
            kv = partner_last_kv.get(old_pid)
            prod = product_map.get(kv) if kv else None
            gross = money(ctr["adag_ar_akcio_ft"]) or money(ctr["adag_ar_ft"])
            if not pid or not prod or not gross:
                continue
            net = round(gross / VAT, 2)
            price_rows[(pid, prod)] = max(net, price_rows.get((pid, prod), 0))
        existing_price_pairs = {
            (row.partner_id, row.product_id)
            for row in (
                await db.execute(select(PartnerPrice.partner_id, PartnerPrice.product_id))
            ).all()
        }
        for (pid, prod), net in price_rows.items():
            if (pid, prod) in existing_price_pairs:
                continue
            db.add(PartnerPrice(partner_id=pid, product_id=prod, price_per_portion=net))
            prices_by_product.setdefault(prod, []).append(net)
        await db.commit()
        log(f"Partner-árak (szerződés-adagár, nettó): {len(price_rows)}")

        # termék alapár: a partner-árak mediánja
        priced = 0
        for prod_id, vals in prices_by_product.items():
            p = await db.get(Product, prod_id)
            if p and not p.price_per_portion:
                p.price_per_portion = round(statistics.median(vals), 2)
                priced += 1
        await db.commit()
        log(f"Termék-alapárak (medián a partner-árakból): {priced}")

        db.add(AuditEvent(
            action="import.xpresso",
            entity_type="import",
            detail={
                "partners": new_partners, "products": new_products + new_parts,
                "assets": new_assets, "tickets": new_tickets,
                "settlements": new_settl, "lines": new_lines,
                "stock": new_stock, "prices": len(price_rows),
                "cutoff": CUTOFF,
            },
        ))
        await db.commit()
        log("KÉSZ.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
