# -*- coding: utf-8 -*-
"""3. lépcső: DELTA-merge a friss (2026-10-05) dumpból az éles Iwfm DB-be.

SEMMIT nem ír felül: csak a még nem importált entitásokat szúrja be —
a meglévőket a stage2 jelölői azonosítják (Partner.notes "Xpresso-ID",
Settlement.note "Xpresso elszámolás: #", ServiceTicket "Xpresso munkalap: #",
Product.notes "Xpresso kód:", Asset.barcode). Többször futtatható.

Forrás: xpresso_staging_full.db (stage1_full_staging.py). Cél: IMPORT_DB_URL.
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import sqlite3
import sys
import uuid
from datetime import UTC, datetime

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

STAGING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xpresso_staging_full.db")
CUTOFF = "2021-08-09"
VAT = 1.27
BATCH = 400

FAJTA_NEV = {
    11: "Kávégép", 12: "Több számlálós kávégép", 13: "Kávégép számláló",
    14: "Kávédaráló", 15: "Vízlágyító",
}
HELY_NEV = {0: "Polc", 1: "Szerviz", 2: "Partner", 3: "Külső raktár",
            4: "Ügyfél", 5: "Ügyfél (cseregép)"}


def dt(s):
    if not s or str(s).startswith("0000"):
        return None
    s = str(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def clean(s, limit=None):
    if s is None:
        return None
    out = str(s).strip()
    if not out or out.upper() == "NULL":
        return None
    return out[:limit] if limit else out


def money(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


async def main() -> None:
    url = os.environ["IMPORT_DB_URL"]
    engine = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    st = sqlite3.connect(STAGING)
    st.row_factory = sqlite3.Row
    rpt: list[str] = []

    def log(msg):
        print(msg, flush=True)
        rpt.append(msg)

    async with Session() as db:
        # ---- meglévő jelölők beolvasása (MIT importáltunk már) ----
        partner_map: dict[int, uuid.UUID] = {}
        existing_partner_names: dict[str, uuid.UUID] = {}
        for pid, name, notes in (
            await db.execute(select(Partner.id, Partner.name, Partner.notes))
        ).all():
            existing_partner_names[(name or "").strip().lower()] = pid
            m = re.search(r"Xpresso-ID: (\d+)", notes or "")
            if m:
                partner_map[int(m.group(1))] = pid

        done_settlements: set[int] = set()
        for (note,) in (
            await db.execute(select(Settlement.note).where(Settlement.note.like("%Xpresso elsz%")))
        ).all():
            m = re.search(r"Xpresso elszámolás: #(\d+)", note or "")
            if m:
                done_settlements.add(int(m.group(1)))

        done_tickets: set[int] = set()
        for (desc,) in (
            await db.execute(
                select(ServiceTicket.description).where(
                    ServiceTicket.description.like("%Xpresso munkalap: #%")
                )
            )
        ).all():
            m = re.search(r"Xpresso munkalap: #(\d+)", desc or "")
            if m:
                done_tickets.add(int(m.group(1)))

        # A termék-note a régi `kod` mezőt tartalmazza, a térkép kulcsa viszont
        # kv_id — a staging adja a kod→kv_id megfeleltetést. (Az első futás
        # ezt kv_id-ként olvasta → 252 duplikátum; fix_dupe_products.py
        # vonta össze őket.)
        kod_to_kv = {
            r["kod"]: r["kv_id"]
            for r in st.execute("SELECT kv_id, kod FROM Term_KV_KAVE")
            if r["kod"] is not None
        }
        product_map: dict[int, uuid.UUID] = {}  # kv_id → uuid
        existing_product_names: set[str] = set()
        for prid, name, notes in (
            await db.execute(select(Product.id, Product.name, Product.notes))
        ).all():
            existing_product_names.add((name or "").strip().lower())
            m = re.search(r"Xpresso kód: (\d+)", notes or "")
            if m and int(m.group(1)) in kod_to_kv:
                product_map[kod_to_kv[int(m.group(1))]] = prid

        asset_map: dict[str, uuid.UUID] = {}
        for aid, bc in (await db.execute(select(Asset.id, Asset.barcode))).all():
            if bc:
                asset_map[bc.strip()] = aid

        codes = [c for c in (await db.execute(select(Partner.partner_code))).scalars() if c]
        nums = [int(c[3:]) for c in codes if c.startswith("PT-") and c[3:].isdigit()]
        next_pt = (max(nums) + 1) if nums else 1
        last_ticket = (
            await db.execute(
                select(ServiceTicket.ticket_no)
                .where(ServiceTicket.ticket_no.like("SZ-%"))
                .order_by(ServiceTicket.ticket_no.desc()).limit(1)
            )
        ).scalar_one_or_none()
        next_sz = 1
        if last_ticket:
            try:
                next_sz = int(last_ticket.split("-", 1)[1]) + 1
            except (IndexError, ValueError):
                pass

        log(f"Jelölők: {len(partner_map)} partner, {len(done_settlements)} elszámolás, "
            f"{len(done_tickets)} szervizjegy, {len(product_map)} kávé-termék, {len(asset_map)} gép")

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
        term_nev: dict[int, str] = {}
        for r in st.execute(
            "SELECT term_id, nev FROM Mozgas_RESZLETES WHERE nev IS NOT NULL ORDER BY MozgReszlID"
        ):
            term_nev[r["term_id"]] = clean(r["nev"], 250) or f"Termék #{r['term_id']}"
        contracts: dict[str, sqlite3.Row] = {}
        for r in st.execute(
            "SELECT * FROM Partner_ELSZAM_SZERZODESEK WHERE aktiv=1 ORDER BY datum_tol"
        ):
            vk = clean(r["vonalkod"], 64)
            if vk:
                contracts[vk] = r
        ceg_kod = {r["partner_id"]: clean(r["ceg_kod"], 8)
                   for r in st.execute("SELECT partner_id, ceg_kod FROM Partner_LISTA")}

        # =====================================================
        # 1) ÚJ PARTNEREK
        # =====================================================
        new_partners = 0
        new_partner_ids: set[uuid.UUID] = set()
        for r in st.execute("SELECT * FROM Partner_LISTA ORDER BY partner_id"):
            if r["partner_id"] in partner_map:
                continue
            name = clean(r["uzlet_neve"], 256) or clean(r["cegnev"], 256)
            if not name:
                continue
            key = name.lower()
            if key in existing_partner_names:
                partner_map[r["partner_id"]] = existing_partner_names[key]
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
                    x for x in (clean(r["uzlet_cime_isz"]), clean(r["uzlet_cime_varos"]),
                                clean(r["uzlet_cime_utca"])) if x
                )[:512] or None,
                billing_address=clean(r["szekhely"], 512),
                notes="\n".join(notes),
                is_active=bool(r["aktiv"]),
            )
            next_pt += 1
            db.add(p)
            partner_map[r["partner_id"]] = p.id
            existing_partner_names[key] = p.id
            new_partner_ids.add(p.id)
            new_partners += 1
        await db.commit()
        log(f"Új partnerek: {new_partners}")

        # =====================================================
        # 2) ÚJ TERMÉKEK (kávé) — alkatrész-katalógus delta nélkül
        # =====================================================
        new_products = 0
        for r in st.execute("SELECT * FROM Term_KV_KAVE ORDER BY kv_id"):
            if r["kv_id"] in product_map:
                continue
            name = clean(r["nev"], 256)
            if not name:
                continue
            if name.lower() in existing_product_names:
                name = f"{name} (X{r['kod']})"[:256]
            deleted = bool(r["torolve"]) if r["torolve"] not in (None, "") else False
            notes = [f"Xpresso kód: {r['kod']}"]
            if clean(r["szemes_orolt"]):
                notes.append(f"Fajta: {clean(r['szemes_orolt'])}")
            pr = Product(
                id=uuid.uuid4(), name=name, unit="kg", grams_per_portion=7,
                price_per_portion=0.0, vat_percent=27, is_active=not deleted,
                notes="\n".join(notes),
            )
            db.add(pr)
            product_map[r["kv_id"]] = pr.id
            existing_product_names.add(name.lower())
            new_products += 1
        await db.commit()
        log(f"Új kávé-termékek: {new_products}")

        # =====================================================
        # 3) ÚJ GÉPEK
        # =====================================================
        new_assets = 0
        for r in st.execute("SELECT * FROM Term_ESZ_VK ORDER BY vonalkod"):
            barcode = clean(r["vonalkod"], 64)
            if not barcode or barcode in asset_map:
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
                    location_type = "Polc"
            elif hely_fajta == 1:
                status = "maintenance"
            notes = [f"Xpresso hely: {HELY_NEV.get(hely_fajta, hely_fajta)}", "Delta-import 2026-10-05"]
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
            if status == "deployed" and deployed_at is None:
                deployed_at = datetime.now(UTC)
            a = Asset(
                id=uuid.uuid4(), barcode=barcode, name=name[:256],
                category=FAJTA_NEV.get(r["fajta_id"]), manufacturer=manu,
                article_number=clean(r["cikkszam"], 64) or tip_cikk,
                serial_number=clean(r["gyart_szam"], 128),
                location_type=location_type,
                counter=int(r["szamlalo"]) if r["szamlalo"] not in (None, "") else None,
                norm=float(r["norma"]) if r["norma"] not in (None, "") else None,
                tangible=bool(r["targyi"]), status=status, partner_id=partner_id,
                deployed_at=deployed_at, notes="\n".join(notes),
            )
            db.add(a)
            db.add(AssetMovement(asset_id=a.id, action="created",
                                 detail="Delta-import a régi (Xpresso) rendszerből"))
            if status == "deployed" and partner_id:
                db.add(AssetMovement(asset_id=a.id, action="deploy", partner_id=partner_id,
                                     detail="Kihelyezés (Xpresso delta-import)"))
            asset_map[barcode] = a.id
            new_assets += 1
        await db.commit()
        log(f"Új gépek: {new_assets}")

        # =====================================================
        # 4) ÚJ SZERVIZJEGYEK
        # =====================================================
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
            if r["szerviz_id"] in done_tickets:
                continue
            created = dt(r["felvetel_datum"]) or datetime.now(UTC)
            finished = dt(r["szerviz_veg_datum"]) or dt(r["kiadas_datum"])
            if finished or r["allapot_id"] == 2:
                status, resolved = "done", finished or created
            elif r["stadium_id"] == 2:
                status, resolved = "cancelled", None
            elif created >= recent_open_limit:
                status, resolved = "open", None
            else:
                status, resolved = "cancelled", None
            hiba = clean(r["hiba"]) or "Szervizmunka"
            desc_parts = [hiba]
            if clean(r["megjegyzes_belso"]):
                desc_parts.append(f"Belső megjegyzés: {clean(r['megjegyzes_belso'])}")
            if munkak.get(r["szerviz_id"]):
                desc_parts.append("Elvégzett munkák: " + "; ".join(munkak[r["szerviz_id"]][:12]))
            vk = clean(r["eszkoz_vonalkod"], 64)
            t = ServiceTicket(
                id=uuid.uuid4(), ticket_no=f"SZ-{next_sz:04d}", kind="repair",
                status=status, priority="normal",
                title=(hiba[:250] + "…") if len(hiba) > 250 else hiba[:256],
                description="\n".join(desc_parts) + f"\nXpresso munkalap: #{r['szerviz_id']}",
                asset_id=asset_map.get(vk) if vk else None,
                asset_label=vk,
                partner_id=partner_map.get(r["partner_id"]),
                partner_label=old_partner_names.get(r["partner_id"]),
                assigned_to_name=users.get(r["dolgozo_user_id"]),
                counter_at_service=int(r["kvg_szamlalo"]) if r["kvg_szamlalo"] not in (None, "") else None,
                resolved_at=resolved, created_at=created,
            )
            db.add(t)
            next_sz += 1
            new_tickets += 1
            if new_tickets % BATCH == 0:
                await db.flush()
        await db.commit()
        log(f"Új szervizjegyek: {new_tickets}")

        # =====================================================
        # 5) ÚJ ELSZÁMOLÁSOK + tételek (befejezett, CUTOFF óta)
        # =====================================================
        gep_info: dict[int, tuple[str | None, int | None]] = {}
        for r in st.execute("SELECT partner_gep_id, vonalkod, partner_id FROM Partner_ELSZAM_SZERZODESEK"):
            gep_info[r["partner_gep_id"]] = (clean(r["vonalkod"], 64), r["partner_id"])
        product_name_by_kv = {
            r["kv_id"]: clean(r["nev"], 256)
            for r in st.execute("SELECT kv_id, nev FROM Term_KV_KAVE")
        }

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

        new_settl = new_lines = 0
        for r in st.execute(
            "SELECT * FROM Elszamolas WHERE befejezve=1 AND elszamolas_end >= ? "
            "ORDER BY elszamolas_end, elszamolas_id", (CUTOFF,)
        ):
            if r["elszamolas_id"] in done_settlements:
                continue
            partner_id = partner_map.get(r["partner_id"])
            created = dt(r["elszamolas_end"]) or dt(r["elszamolas_start"])
            if not partner_id or not created:
                continue
            total_gross = money(r["osszes_fizetendo"])
            fizetve = money(r["fizetve"])
            tartozas = money(r["tartozas"])
            notes = [f"Xpresso elszámolás: #{r['elszamolas_id']}"]
            if r["kv_id"] in product_name_by_kv and product_name_by_kv[r["kv_id"]]:
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
                id=uuid.uuid4(), partner_id=partner_id,
                settled_by_name=users.get(r["user_id"], "Ismeretlen"),
                invoicing_company=ceg_kod.get(r["partner_id"]),
                payment_method="cash",
                total_net=round(total_gross / VAT, 2), total_gross=total_gross,
                invoiced=False, payment_status="none",
                note="\n".join(notes), created_at=created,
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
                        settlement_id=s.id, product_id=product_map.get(r["kv_id"]),
                        product_name=label[:256],
                        previous_qty=0.0, physical_qty=0.0, consumed_qty=0.0,
                        portions=adag, price_per_portion=round(adag_ar / VAT, 2),
                        vat_percent=27,
                        amount_net=round((osszeg or adag * adag_ar) / VAT, 2),
                    ))
                    new_lines += 1
                if money(g["berleti_dij"]):
                    db.add(SettlementLine(
                        settlement_id=s.id,
                        product_name=(f"Bérleti díj — gép {vk}" if vk else "Bérleti díj")[:256],
                        previous_qty=0.0, physical_qty=0.0, consumed_qty=0.0,
                        portions=1.0, price_per_portion=round(money(g["berleti_dij"]) / VAT, 2),
                        vat_percent=27, amount_net=round(money(g["berleti_dij"]) / VAT, 2),
                    ))
                    new_lines += 1
            for t in term_sorok.get(r["elszamolas_id"], []):
                darab = money(t["darab"])
                elad_ar = money(t["elad_ar"])
                if not darab:
                    continue
                kedv = money(t["kedvezmeny"])
                net = round(darab * elad_ar * (1 - kedv / 100.0), 2)
                db.add(SettlementLine(
                    settlement_id=s.id,
                    product_name=term_nev.get(t["termek_id"], f"Termék #{t['termek_id']}"),
                    previous_qty=0.0, physical_qty=0.0, consumed_qty=darab,
                    portions=darab, price_per_portion=elad_ar, vat_percent=27,
                    amount_net=net,
                ))
                new_lines += 1
            if new_settl % BATCH == 0:
                await db.flush()
                await db.commit()
        await db.commit()
        log(f"Új elszámolások: {new_settl}, tétel: {new_lines}")

        # =====================================================
        # 6) ÚJ PARTNEREK nyitókészlete + partner-árak (csak ÚJ pároknál)
        # =====================================================
        stock_seen = {
            (row.partner_id, row.product_id)
            for row in (await db.execute(select(PartnerStock.partner_id, PartnerStock.product_id))).all()
        }
        new_stock = 0
        for r in st.execute(
            "SELECT e.partner_id, e.kv_id, e.keszlet FROM Elszamolas e JOIN ("
            " SELECT partner_id, MAX(elszamolas_end) me FROM Elszamolas WHERE befejezve=1 GROUP BY partner_id"
            ") m ON m.partner_id=e.partner_id AND m.me=e.elszamolas_end WHERE e.befejezve=1"
        ):
            pid = partner_map.get(r["partner_id"])
            # KIZÁRÓLAG a most létrehozott partnereknél — élő készletet nem bántunk!
            if pid not in new_partner_ids:
                continue
            qty = money(r["keszlet"])
            prod = product_map.get(r["kv_id"])
            if not pid or not prod or qty <= 0 or (pid, prod) in stock_seen:
                continue
            stock_seen.add((pid, prod))
            db.add(PartnerStock(partner_id=pid, product_id=prod, quantity=qty))
            db.add(StockMovement(partner_id=pid, product_id=prod, action="replenish",
                                 quantity_delta=qty, note="Xpresso nyitókészlet (delta-import)"))
            new_stock += 1
        await db.commit()
        log(f"Új partner-készletek: {new_stock}")

        existing_price_pairs = {
            (row.partner_id, row.product_id)
            for row in (await db.execute(select(PartnerPrice.partner_id, PartnerPrice.product_id))).all()
        }
        partner_last_kv: dict[int, int] = {}
        for r in st.execute(
            "SELECT partner_id, kv_id FROM Elszamolas WHERE befejezve=1 ORDER BY elszamolas_end"
        ):
            partner_last_kv[r["partner_id"]] = r["kv_id"]
        new_prices = 0
        changed_prices: list[str] = []
        for vk, ctr in contracts.items():
            old_pid = ctr["partner_id"]
            pid = partner_map.get(old_pid)
            kv = partner_last_kv.get(old_pid)
            prod = product_map.get(kv) if kv else None
            gross = money(ctr["adag_ar_akcio_ft"]) or money(ctr["adag_ar_ft"])
            if not pid or not prod or not gross:
                continue
            net = round(gross / VAT, 2)
            if (pid, prod) in existing_price_pairs:
                continue  # meglévő árat NEM írunk felül
            existing_price_pairs.add((pid, prod))
            db.add(PartnerPrice(partner_id=pid, product_id=prod, price_per_portion=net))
            new_prices += 1
        await db.commit()
        log(f"Új partner-árak: {new_prices}")

        db.add(AuditEvent(
            action="import.xpresso_delta",
            entity_type="import",
            detail={
                "dump": "2026-10-05", "partners": new_partners, "products": new_products,
                "assets": new_assets, "tickets": new_tickets,
                "settlements": new_settl, "lines": new_lines,
                "stock": new_stock, "prices": new_prices,
            },
        ))
        await db.commit()
        log("DELTA KÉSZ.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
