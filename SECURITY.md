# Biztonsági audit — Iwfm v0.1 (2026-06-11)

Kézi kódaudit az első kiadás teljes kódbázisán. Állapot: ✅ rendben · 📌 elfogadott
kockázat (dokumentálva) · minden megállapításhoz a kezelés módja.

## Adatvédelem (GDPR)

- ✅ **Érzékeny PII titkosítva nyugalmi állapotban.** Adóazonosító jel, TAJ,
  bankszámlaszám, bér: Fernet (AES-128-CBC + HMAC-SHA256), `LargeBinary`
  oszlopok ([crypto.py](backend/app/core/crypto.py)). Plaintext sosem kerül DB-be
  vagy logba. Production a `WFM_ENC_KEY` nélkül **el sem indul**.
- ✅ **Maszkolt megjelenítés.** A listák/adatlapok csak `••• 1234` formát adnak;
  a teljes érték kizárólag a `GET /api/employees/{id}/reveal` admin-végponton
  kérhető, ami **minden alkalommal auditeseményt ír** (`employee.reveal`).
- ✅ **Audit-napló.** Append-only `audit_events`: létrehozás, módosítás (mezőlista),
  felfedés, közzététel, 96h/168h szabály-felülbírálás, kézi órakorrekció,
  bérexport, login, bootstrap — actor + IP + időbélyeg.
- ✅ **Checksum-validáció beviteli ponton** (adóazonosító mod-11, TAJ 3/7 mod-10,
  GIRO 9-7-3-1): elgépelt azonosító be sem kerülhet.
- 📌 Az auditnapló nem hash-láncolt (tamper-evidens) — v1-ben DB-jogosultsággal
  módosítható. Tervezett: hash-lánc vagy WORM-export későbbi verzióban.

## Autentikáció és session

- ✅ **Argon2id** jelszó-hash (OWASP-ajánlott paraméterek, argon2-cffi).
- ✅ **JWT httpOnly cookie-ban** (`SameSite=Lax`, prod: `Secure`), 12h lejárat.
  XSS nem fér hozzá a tokenhez; cross-site POST nem viszi a cookie-t.
- ✅ **Login throttle:** 5 hibás kísérlet / 15 perc (email+IP), egységes
  hibaüzenet (user-enumeration ellen), sikeres login auditálva.
- ✅ **Bootstrap csak üres users táblánál** működik, utána 403.
- ✅ Inaktivált fiók tokenje azonnal érvénytelen (minden kérésnél DB-ellenőrzés).
- 📌 JWT-visszavonási lista nincs (logout = cookie törlés); kompromittált token a
  lejáratig él. Mitigáció: 12h lejárat + fiók inaktiválás azonnal hat.
- 📌 A login-throttle process-memóriában él — több instance-nál instance-onként
  számol. Single-instance Railway deployra méretezve.

## Hozzáférés-kezelés

- ✅ **Minden végpont szerepkör-őrzött** (`require_role`): employee < manager < admin.
  Negatív tesztek bizonyítják (403) — lásd test_employees/test_timeoff/test_timeclock.
- ✅ **IDOR-mentes önkiszolgálás:** a `/api/me/*` végpontok a JWT-ből oldják fel
  a dolgozót, idegen `employee_id` nem is adható át
  ([me.py](backend/app/api/me.py), teszt: test_me.py).
- ✅ Dolgozó a **publikálatlan (draft) beosztást nem látja** (teszt bizonyítja).

## Webes támadási felületek

- ✅ **SQL injection:** kizárólag SQLAlchemy paraméterezett lekérdezések.
- ✅ **XSS:** React automatikus escape; `dangerouslySetInnerHTML` nincs.
- ✅ **CSRF:** SameSite=Lax cookie + szigorú CORS (csak a frontend origin,
  JSON-only végpontok → preflight kötelező).
- ✅ **Security headerek:** nosniff, X-Frame-Options DENY, Referrer-Policy,
  prodban HSTS ([main.py](backend/app/main.py)).
- ✅ OpenAPI/docs **prodban kikapcsolva**; hibaválaszok nem szivárogtatnak stacket.
- ✅ Export fájlnév szerver-oldalon képzett (nincs header-injection a
  Content-Disposition-ben).

## Titkok kezelése

- ✅ `.env` gitignore-olva; `.env.example` placeholder-ekkel.
- ✅ Production indulási guard: `WFM_SECRET_KEY` és `WFM_ENC_KEY` kötelező,
  dev-default kulccsal a prod **nem indul el**.
- ✅ Generált kezdeti jelszó egyetlen egyszer jelenik meg a create válaszban
  (HTTPS), nem tárolódik sehol plaintextben.

## Ismert, tudatosan vállalt v1-korlátok

1. Egy-bérlős (single-tenant) modell — bérlő-izoláció a Mira-integrációs
   fázisban kerül be.
2. Jelszócsere-folyamat (dolgozói self-service) még nincs — az admin tud új
   jelszót adni; következő verzió.
3. Audit-export UI még nincs (az adat megvan a táblában).
