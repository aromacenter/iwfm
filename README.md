# Iwfm — Munkaerő-kezelő rendszer

Önálló workforce management alkalmazás magyar munkajogi (Mt.) megfeleléssel:
dolgozói törzsadat-nyilvántartás, heti beosztás, távollét-kezelés, jelenléti ív
és bérszámfejtési export. A dolgozók a telefonjukon, böngészőből látják a saját
beosztásukat és ott jelentkeznek be/ki.

**Stack:** FastAPI + SQLAlchemy (async) + PostgreSQL · Next.js + Tailwind ·
Railway deploy · GitHub Actions CI

## Funkciók

| Modul | Mit tud |
|---|---|
| **Dolgozók** | Teljes HR törzsadat a bérszámfejtéshez: név, anyja neve, születési adatok, lakcím, **adóazonosító jel, TAJ, bankszámlaszám, bér** (Fernet-titkosítva, maszkolva, auditált felfedéssel), FEOR-kód, munkaviszony adatok. Checksum-validáció minden magyar azonosítóra. |
| **Beosztás** | Heti rács (Microsoft Shifts-minta), piszkozat → közzététel folyamat. **Mt. megfelelőség-motor:** max 12 óra/nap és 48 óra/hét (99.§), 11 óra napi pihenő (104.§), 48 óra heti pihenő + 6 nap szabály (105–106.§), 168 órás közlési határidő (97.§ (4)), 96 órás módosítási szabály (97.§ (5)). Hiba blokkol, figyelmeztetés megerősítést kér (auditálva). |
| **Távollét** | Szabadság/betegszabadság/egyéb kérelmek, jóváhagyás, a jóváhagyott távollét blokkolja a beosztást. |
| **Jelenlét** | Dolgozói be-/kijelentkezés telefonról, kézi korrekció (auditálva), ledolgozott órák. |
| **Bérexport** | CSV/XLSX időszaki export bármely magyar bérprogramhoz: azonosítók + beosztott/ledolgozott órák + távollét napok. |
| **Önkiszolgálás** | A dolgozó saját közölt beosztása, távollét-kérelem, óra — mobilra optimalizálva. |

Szerepkörök: `admin` (minden + érzékeny adat), `manager` (beosztás, távollét,
jelenlét, export), `employee` (csak saját adatok).

## Helyi futtatás

```bash
# Backend (Python 3.12+)
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements-dev.txt
uvicorn app.main:app --port 8000
# SQLite-tal fut alapból, konfiguráció nélkül.

# Frontend (Node 22+)
cd frontend
npm ci
npm run dev
# http://localhost:3000 — első indításkor "Admin fiók létrehozása"
```

Tesztek: `cd backend && python -m pytest` (74 teszt, valós adatbázis ellen).

## Deploy — Railway + GitHub

1. **GitHub:** pushold ezt a repót GitHubra.
2. **Railway projekt:** [railway.app](https://railway.app) → New Project → Deploy from GitHub repo.
3. **PostgreSQL:** Add hozzá a Postgres plugint (a `DATABASE_URL`-t automatikusan megkapja a backend).
4. **Backend service:** Root Directory = `backend` (Dockerfile-t és `railway.json`-t automatikusan felismeri). Env varok:
   - `WFM_SECRET_KEY` — `python -c "import secrets; print(secrets.token_urlsafe(48))"`
   - `WFM_ENC_KEY` — `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `WFM_FRONTEND_ORIGIN` — a frontend publikus URL-je (pl. `https://iwfm-frontend.up.railway.app`)
   - `WFM_COOKIE_SECURE` = `true`
5. **Frontend service:** Root Directory = `frontend`. Env var:
   - `NEXT_PUBLIC_API_URL` — a backend publikus URL-je (build-időben ég be!)
6. Nyisd meg a frontend URL-t → "Első indítás? Admin fiók létrehozása".

> ⚠️ A `WFM_ENC_KEY` az érzékeny személyes adatok (adóazonosító, TAJ, bankszámla,
> bér) titkosítókulcsa. Mentsd el biztonságos helyre — elvesztésekor ezek az
> adatok visszafejthetetlenné válnak. Production a kulcsok nélkül el sem indul.

## Jogi megfelelés

- **Mt. (2012. évi I. tv.)** beosztási szabályok kódolva a
  [compliance.py](backend/app/services/wfm/compliance.py)-ban, §-hivatkozásokkal.
- **GDPR:** érzékeny azonosítók titkosítva (AES-128-CBC + HMAC, Fernet), minden
  hozzáférés/módosítás/export auditnaplóba kerül (`audit_events`), a válaszok
  maszkolt értéket adnak, a teljes értéket csak admin kérheti le (auditálva).
- A szoftver **nem helyettesít** munkajogi/adatvédelmi szakértőt; az üzemeltető
  felelőssége az adatkezelési tájékoztató és a jogalap megléte.

## Mira integráció (későbbi fázis)

Az app szándékosan önálló (saját auth + DB). A Mira-hoz illesztés terve:
SSO a Mira JWT-jével + "Iwfm" nav-bejegyzés a Mira oldalsávjában, vagy
beágyazás reverse-proxy mögé. Az API-felület ehhez nem igényel változást.
