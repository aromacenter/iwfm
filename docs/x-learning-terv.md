# X-Learning – jóváhagyott terv (2026-11-02, 142. kör, kivitelezés: 2026-11-03)

## Döntések (a user válaszai)
1. Tananyag: formázott szöveg a felületen **és** feltölthető csatolmány (PDF/kép).
2. Videó: **YouTube-link beágyazva** (nincs feltöltés).
3. **Egy tananyag = egy lecke.**
4. **Határidő** a kiosztásnál, **érvényesség hónapokban** (lejárat után újra el kell végezni, emlékeztetővel).
5. Teszt: **feleletválasztós, több helyes is lehet.**
6. **Korlátlan próbálkozás, mind naplózva; elvégzett = 100 %.**
7. Fix kérdéssor **és** véletlen N a kérdésbankból (anyagonként választható).
8. Nincs időkorlát.
9. Kiosztás **egyénileg és szerepkör szerint** (a későbbi belépők automatikusan kapják: `auto_roles`).
10. Csak saját dolgozók; **külsős csak akkor, ha az anyagon engedélyezett** (`external_ok`).
11. Partner-oldali (ügyfél) oktatóanyag: külön kategória, **később**.
12. **Oklevél PDF** (XL-ÉÉÉÉ-NNNN sorszám, cégnév/logó, dolgozó, anyag, dátum, érvényesség).
13. Képzés-napló: vezetői nézet **és** CSV export a bérexport mellé.
14. Emlékeztető: **Telegram a dolgozónak** (3 nappal előtte, lejáratkor, érvényesség-lejáratkor) **és** sor a heti vezetői digestben.
15. Központi tananyag-csomag a Flottából: **később**.
16. **`learning` modulkapcsoló + külön ár** (a pultban csomagként / funkcióként, az árat a user adja meg).
17. Név: **X-Learning**.

## Kiegészítések (2026-11-02 este)
- **Mindenki csak a saját kartonját látja**: a dolgozói API (`/api/learning/my…`) a belépett dolgozó azonosítójából dolgozik, idegen kiosztás-azonosítóra 404. A szerepkör szerinti kiosztás is dolgozónkénti sorokat hoz létre.
- **Külön felület, nem az X-admin menüje (A-variáns):** `learning.x-admin.hu` = kapu a Flottán (a dolgozó megadja a cége rendszerét, pl. program.x-presso.hu → átirányítás a példány `/learning` útjára). A tanulói felület minden példányban saját arculattal fut (nincs AppShell), saját belépő-oldallal.
- **Minden dolgozó be tud lépni** a meglévő fiókjával (e-mail + jelszó, mint az önkiszolgálónál). Fiókot a vezető ad a Dolgozók menüben; automatikusan NEM hozunk létre.
- **A tanulói belépés számít a csomag fiók-limitjébe** (user döntése).
- **Admin belépő ugyanazon a felületen:** a `learning` jogosultsággal (alapból admin + manager) a tanulói felületen megjelenik a „Szerkesztés / Eredmények” rész (`/learning/admin`); az X-admin menüből egy link oda mutat (nem építjük kétszer).

## Már megírt, de még NEM bekötött kód (2026-11-02)
- `backend/app/api/learning.py` – teljes API (my-végpontok, vezetői rész, assign, results, overview, export.csv, run_learning_reminders).
- `backend/app/services/wfm/learning_pdf.py` – oklevél PDF.
- `scratchpad/patch_r142_learning_backend.py` – modellek (LearningMaterial/Attachment/Question/Assignment/Attempt), migráció `c6d7e8f9a0b1` (revises b5c6d7e8f9a0), `learning` modul + jogosultság, router-regisztráció, notifier-horog, Flotta modul-lista. **Még nem futott.** Futtatás után: `test_mega_round2` várt dict bővül `"learning": 0`-val.

## Hátra van holnapra
1. Patch futtatása, ruff, migráció fel/le, tesztek (`tests/test_round142_learning.py`: kiosztás egyéni + szerepkör, külsős tiltás, saját karton izoláció 404, teszt 100 %/részleges, érvényesség, oklevél PDF, export, emlékeztető).
2. Frontend: `app/learning/layout.tsx` (saját arculat, AppShell nélkül), `app/learning/login/page.tsx`, `app/learning/page.tsx` (saját karton), `app/learning/[id]/page.tsx` (anyag + YouTube iframe + csatolmány + teszt + oklevél), `app/learning/admin/page.tsx` (anyag-szerkesztő, kérdésbank, kiosztás, eredmény-tábla, CSV); nav-link az X-adminban (perm `learning`, module `learning`); i18n `learning.*` hu/en.
3. Flotta: `/learning` kapu-oldal (cég-azonosító → átirányítás), `learning.x-admin.hu` domain a flotta-szolgáltatásra (Railway custom domain + Cloudflare rekord), modul-lista már kész; csomagok/ár (lásd lent).
4. Roadmap: „X-Learning modul” → Elkészült; memória frissítése.

## Landing + csomagok — KÉSZ (2026-11-02 este, Flotta commit 45cd241)
- Landing: nav-link + „X-Learning” szekció (4 kártya) + csomag-kártyák, funkció-kártya a Funkciók rácsban.
- Pult: Csomagok fül → „📚 X-Learning csomagok” (app=learning; a Fiók oszlop = tanulói belépők száma, dolgozó nem használt).
- Seed irányárak: xl-s 10 fiók 9 900 Ft/hó, xl-m 30 fiók 19 900 Ft/hó, xl-l korlátlan 34 900 Ft/hó (bruttó) — a user átírja.
- NINCS még: X-Learning csomag hozzárendelése példányhoz (az instance egy plan-mezős) és a fiók-limit számítása a tanulói belépőkre — ezt a kivitelezés részeként kell megoldani (pl. instance.learning_plan + a licenc-push `learning_max_users`).

## KÉSZ — 2026-11-03 (142. kör kivitelezve)
- Backend: modellek + migrációk `c6d7e8f9a0b1` (learning_*) és `d7e8f9a0b1c2` (licenc: learning_plan, learning_max_users), `learning` modul + jog (admin, manager), router `/api/learning`, notifier (Telegram-emlékeztető + heti digest-sor), oklevél PDF. Tesztek: `tests/test_round142_learning.py`.
- Fiók-limit: a licenc `learning_max_users` értéke HOZZÁADÓDIK a csomag fiók-limitjéhez (korlátlan X-Learning = korlátlan fiók); a Flotta küldi a katalógusból.
- Frontend: `/learning` (saját karton), `/learning/login`, `/learning/[id]` (szöveg + YouTube + csatolmány + teszt + oklevél), `/learning/admin` (szerkesztő, kérdésbank, csatolmányok, kiosztás, eredmények, CSV); AppShell-nélküli keret saját X-Learning arculattal; X-admin menü: 🎓 X-Learning (mindenki) + 📖 X-Learning kezelés (`learning` jog).
- Flotta: `instance.learning_plan`, licenc-modal „X-Learning csomag” választó (a learning + employees modul automatikusan bekapcsol), pult-chip, `/learning` kapu-oldal (`static/learning.html`), `learning.*` hoszton a gyökér is a kapu, `POST /api/flotta/domain` a saját domain felvételéhez (Railway + Cloudflare).
- Nem készült (később, a terv szerint): partner-oldali oktatóanyag-kategória, központi tananyag-csomag a Flottából.
