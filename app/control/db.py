"""Silnik i sesje control plane (SQLAlchemy async) oraz uruchamianie migracji Alembic.

Sesja jest tworzona per zadanie HTTP (`get_db`); commit jest jawny w handlerze. SQLite dostaje pragmy:
`foreign_keys=ON` (kaskady), `journal_mode=WAL` i `busy_timeout` (rownolegle zadania w tle + zadania HTTP).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import event, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import PROJECT_ROOT, Settings

log = logging.getLogger(__name__)

ALEMBIC_DIR = PROJECT_ROOT / "alembic"
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def resolve_db_url(settings: Settings) -> str:
    """URL bazy z konfiguracji; wzgledna sciezka SQLite jest liczona od katalogu projektu (nie od cwd)."""
    url = make_url(settings.daas_control_db_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        db_path = Path(url.database)
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = url.set(database=str(db_path))
    return url.render_as_string(hide_password=False)


def create_engine(settings: Settings) -> AsyncEngine:
    """Tworzy silnik async. Nie laczy sie z baza (polaczenie powstaje przy pierwszym uzyciu)."""
    url = resolve_db_url(settings)
    engine = create_async_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Zaleznosc FastAPI: sesja per zapytanie. Commit robi handler; niezatwierdzone zmiany sa cofane."""
    async with request.app.state.db_sessionmaker() as session:
        yield session


# --------------------------------------------------------------------------- migracje
def alembic_config(url: str):
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade_sync(settings: Settings, revision: str = "head") -> None:
    """`alembic upgrade <revision>` — blokujace; env.py otwiera wlasna petle zdarzen, wiec nie wolno
    wolac tego z watku z dzialajaca petla (uzyj `upgrade`)."""
    from alembic import command

    command.upgrade(alembic_config(resolve_db_url(settings)), revision)


async def upgrade(settings: Settings, revision: str = "head") -> None:
    """Migracje poza petla zdarzen aplikacji (osobny watek)."""
    await asyncio.to_thread(upgrade_sync, settings, revision)
    log.info("control db migrated to %s", revision)
