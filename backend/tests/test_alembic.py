"""Alembic: a baseline migráció a modellekkel azonos sémát épít, a meglévő
adatbázist pedig a boot-logika DDL nélkül bélyegzi meg."""

from __future__ import annotations

import pathlib

from sqlalchemy import create_engine, inspect, text

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _alembic_cfg(sync_conn):
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["connection"] = sync_conn
    return cfg


def test_upgrade_matches_metadata(tmp_path):
    """Friss DB: upgrade head után az autogenerate nem talál eltérést."""
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'mig.db'}")
    with engine.begin() as conn:
        command.upgrade(_alembic_cfg(conn), "head")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": False})
        diff = compare_metadata(ctx, Base.metadata)
    engine.dispose()
    assert diff == [], diff


def test_existing_db_gets_stamped(tmp_path):
    """Alembic előtti DB (create_all): a boot bélyegez, nem futtat DDL-t,
    második futásra pedig no-op upgrade."""
    from app.main import _run_alembic
    from app.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        _run_alembic(conn)
    with engine.connect() as conn:
        tables = inspect(conn).get_table_names()
        assert "alembic_version" in tables
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version is not None

    # második boot: már verziózott → upgrade head (nincs teendő, nem hasal el)
    with engine.begin() as conn:
        _run_alembic(conn)
    engine.dispose()


def test_fresh_db_created_by_upgrade(tmp_path):
    """Teljesen üres DB: a boot-logika upgrade-del építi fel a teljes sémát."""
    from app.main import _run_alembic

    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    with engine.begin() as conn:
        _run_alembic(conn)
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    engine.dispose()
    assert {"users", "partners", "settlements", "alembic_version"} <= tables
