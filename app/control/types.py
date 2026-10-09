"""Typy kolumn wspolne dla SQLite i Postgresa."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Biezacy czas UTC ze strefa (jedyne zrodlo czasu w control plane)."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """DateTime przechowywany jako UTC bez strefy; po odczycie zawsze z `tzinfo=UTC`.

    SQLite nie zachowuje strefy, wiec bez tego porownania `expires_at < utcnow()` rzucalyby TypeError.
    Naiwne daty na wejsciu sa traktowane jako UTC.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=UTC)
