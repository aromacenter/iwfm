"""Application settings, loaded from environment variables.

Railway supplies DATABASE_URL for the attached PostgreSQL plugin; locally we
fall back to a SQLite file so the app boots with zero configuration.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # --- core ---
    app_name: str = "Iwfm"
    environment: str = "development"  # development | production
    secret_key: str = "dev-only-secret-change-me-0123456789abcdef"  # JWT signing (HS256)
    # Fernet key for PII at-rest encryption (adóazonosító, TAJ, bankszámla, bér).
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    enc_key: str = ""

    # --- database ---
    database_url: str = "sqlite+aiosqlite:///./iwfm.db"

    # --- web ---
    frontend_origin: str = "http://localhost:3000"
    cookie_secure: bool = False  # set True in production (HTTPS)
    session_hours: int = 12
    # Ha be van állítva, az első-admin bootstrap CSAK ezzel a tokennel hívható
    # (megvédi a nyitott bootstrap-ablakot, ha a users tábla valaha kiürülne).
    bootstrap_token: str = ""
    # Üzemeltetői (Flotta-pult) token: a licenc táv-kezeléséhez. Üresen a
    # /api/operator végpontok zárva vannak.
    operator_token: str = ""
    # GitHub Actions → X-admin: az ügynök-APK automatikus közzététele (WFM_AGENT_PUBLISH_TOKEN)
    agent_publish_token: str = ""
    # Modul-alapértelmezés, amíg nincs licenc-sorban beállítás: None (nincs
    # env) = minden modul (saját példány); beállítva = vesszős lista, üres
    # string = csak az alap-funkciók (bérelt példány így születik).
    default_modules: str | None = None
    # Platform-SMTP tartalék (a Flotta adja át provisioningkor): amíg az
    # ügyfél nem állít be saját SMTP-t, a rendszer-levelek (jelszó-
    # emlékeztető) ezen mennek. Üres host = nincs tartalék.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True

    model_config = {"env_prefix": "WFM_", "env_file": ".env", "extra": "ignore"}

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def sqlalchemy_url(self) -> str:
        """Normalize Railway's postgres:// scheme to the asyncpg driver."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    import os

    # Railway injects DATABASE_URL without our prefix — honor it if present.
    if os.getenv("DATABASE_URL") and not os.getenv("WFM_DATABASE_URL"):
        os.environ["WFM_DATABASE_URL"] = os.environ["DATABASE_URL"]
    settings = Settings()
    default_secret = settings.secret_key == "dev-only-secret-change-me-0123456789abcdef"
    # Fail-closed: a publikus (repóban lévő) fejlesztői kulccsal csak helyi,
    # SQLite-os futás engedett. Bármilyen "igazi" adatbázis (Postgres) mögött
    # a kulcsoknak lenniük kell, akkor is, ha a WFM_ENVIRONMENT nincs beállítva —
    # különben hamisított JWT-vel admin-átvétel, és a PII-titkosítás kulcsa is
    # a nyilvános secret-ből származna.
    real_db = not settings.database_url.startswith("sqlite")
    if settings.is_production or real_db:
        where = "production" if settings.is_production else "non-SQLite database"
        if default_secret:
            raise RuntimeError(f"WFM_SECRET_KEY must be set ({where}).")
        if not settings.enc_key:
            raise RuntimeError(f"WFM_ENC_KEY must be set ({where}; PII encryption).")
    if settings.is_production and not settings.cookie_secure:
        # HTTPS mögött a session-süti mindig secure — ne múljon külön kapcsolón.
        settings.cookie_secure = True
    logger.info(
        "Iwfm settings: environment=%s cookie_secure=%s db=%s",
        settings.environment, settings.cookie_secure, "sqlite" if not real_db else "server",
    )
    return settings
