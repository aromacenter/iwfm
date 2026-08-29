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
    if settings.is_production:
        if settings.secret_key == "dev-only-secret-change-me-0123456789abcdef":
            raise RuntimeError("WFM_SECRET_KEY must be set in production.")
        if not settings.enc_key:
            raise RuntimeError("WFM_ENC_KEY must be set in production (PII encryption).")
    return settings
