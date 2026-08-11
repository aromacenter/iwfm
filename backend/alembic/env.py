"""Alembic környezet — az Iwfm Base metadatához kötve.

Két üzemmód:
1. Programozott (app-boot): a main.py lifespan a már megnyitott sync
   kapcsolatot adja át config.attributes["connection"]-ben — ilyenkor a
   tranzakciót és a loggingot a hívó birtokolja.
2. CLI (fejlesztés, autogenerate): sync engine a WFM_DATABASE_URL /
   DATABASE_URL / alembic.ini sqlalchemy.url alapján; az async drivereket
   (aiosqlite, asyncpg) a beépített sync megfelelőre cseréljük.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import Base  # noqa: E402

config = context.config
target_metadata = Base.metadata

# Programozott módban nem nyúlunk a hívó (uvicorn) logging-beállításaihoz.
if config.attributes.get("connection") is None and config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _sync_url() -> str:
    url = (
        os.getenv("WFM_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
        or "sqlite:///./iwfm.db"
    )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url.replace("+aiosqlite", "").replace("+asyncpg", "")


def _configure_and_run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite nem tud minden ALTER-t — a batch mód táblát újraépítve old meg
        # oszlopmódosítást/-törlést; Postgresen nincs hatása.
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(), target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure_and_run(connection)
        return
    engine = create_engine(_sync_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        _configure_and_run(conn)
        conn.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
