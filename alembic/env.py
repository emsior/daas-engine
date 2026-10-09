"""Srodowisko Alembic (async). Celem autogeneracji jest `app.control.models.Base.metadata`.

URL: `sqlalchemy.url` z konfiguracji (ustawiany przez `app.control.db.upgrade`), a gdy pusty —
DAAS_CONTROL_DB_URL przez `Settings` (wywolanie `alembic upgrade head` z linii polecen).
Logowanie aplikacji nie jest nadpisywane (brak `fileConfig`).
"""
from __future__ import annotations

import asyncio

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.control import models
from app.control.db import resolve_db_url
from app.core.config import get_settings

config = context.config
target_metadata = models.Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or resolve_db_url(get_settings())


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite: ALTER przez przebudowe tabeli
        compare_type=True,
        literal_binds=connection is None,
    )


def run_migrations_offline() -> None:
    _configure(url=_url())
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
