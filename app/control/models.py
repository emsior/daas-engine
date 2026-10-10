"""Modele control plane (SQLAlchemy 2.0, deklaratywne, typowane).

Kontrakt: SPEC v2 sekcja 2.2. Dane klienta (CSV, raporty, metryki) NIE leza w tej bazie — tylko
metadane. Klucze API, tokeny logowania i zaproszenia sa przechowywane wylacznie jako skrot SHA-256.
Migracje: `alembic/versions/`; test `tests/test_control_plane.py` pilnuje zgodnosci modeli z migracja.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.control.ids import new_ulid
from app.control.types import UTCDateTime, utcnow


class Base(DeclarativeBase):
    """Baza deklaratywna; `Base.metadata` jest celem autogeneracji Alembic."""


_ID = String(26)
_CASCADE = "CASCADE"


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_ulid)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(16), default="free")  # free | pro | selfhost
    dpa_accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    dpa_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    locale: Mapped[str] = mapped_column(String(8), default="pl")
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    llm_narrative: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_ulid)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # zawsze male litery
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    locale: Mapped[str] = mapped_column(String(8), default="pl")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"

    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete=_CASCADE), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete=_CASCADE), primary_key=True)
    role: Mapped[str] = mapped_column(String(8))  # owner | admin | member
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (Index("ix_memberships_user_id", "user_id"),)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_ulid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete=_CASCADE))
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(8), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 hex; sam klucz nigdy nie jest zapisywany
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str | None] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (Index("ix_api_keys_org_id", "org_id"),)


class MagicLink(Base):
    __tablename__ = "magic_links"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete=_CASCADE))
    org_id: Mapped[str | None] = mapped_column(ForeignKey("orgs.id", ondelete="SET NULL"), nullable=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_ulid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete=_CASCADE))
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(8))
    token_hash: Mapped[str] = mapped_column(String(64))
    invited_by: Mapped[str | None] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (Index("ix_invitations_org_id", "org_id"), Index("ix_invitations_email", "email"))


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # = nazwa pliku w uploads/ (bez rozszerzenia)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete=_CASCADE))
    kind: Mapped[str] = mapped_column(String(8))  # csv | xlsx | package
    filename: Mapped[str] = mapped_column(String(128))  # nazwa od klienta, wylacznie do wyswietlania
    bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))  # skrot pliku w postaci, w jakiej przyszedl
    path: Mapped[str] = mapped_column(String(64))  # nazwa pliku wzgledem uploads/ organizacji, nie sciezka
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    retention_until: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_uploads_org_id", "org_id"),)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_ulid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete=_CASCADE))
    pipeline: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued|running|succeeded|failed|cancelled
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(String(200), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    upload_id: Mapped[str | None] = mapped_column(
        ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    force_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "user:<id>" | "key:<id>"
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

    __table_args__ = (Index("ix_jobs_org_id_created_at", "org_id", "created_at"), Index("ix_jobs_status", "status"))


class RunRecord(Base):
    """Slad runu w control plane: hashe raportow do weryfikacji (`POST /verify`). Tresci raportow tu nie ma."""

    __tablename__ = "run_records"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete=_CASCADE))
    pipeline: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12))  # SUCCESS | PARTIAL | FAILED
    job_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    # `report_sha256` {html, md, json} rozbite na kolumny, zeby `verify` dzialalo przez indeks, nie skan.
    sha256_html: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256_md: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256_json: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # zakres dat w danych runu (ecommerce_demo: min/max daty zamowienia; uksc_evidence: data zbioru paczki); NULL = brak
    period_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        Index("ix_run_records_org_id_created_at", "org_id", "created_at"),
        Index("ix_run_records_org_sha_html", "org_id", "sha256_html"),
        Index("ix_run_records_org_sha_md", "org_id", "sha256_md"),
        Index("ix_run_records_org_sha_json", "org_id", "sha256_json"),
    )

    @property
    def report_sha256(self) -> dict[str, str | None]:
        return {"html": self.sha256_html, "md": self.sha256_md, "json": self.sha256_json}


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_ulid)
    org_id: Mapped[str | None] = mapped_column(ForeignKey("orgs.id", ondelete=_CASCADE), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(8))  # user | key | system
    actor_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # wylacznie klucze z AUDIT_META_KEYS

    __table_args__ = (Index("ix_audit_events_org_id_id", "org_id", "id"),)


class UsageCounter(Base):
    __tablename__ = "usage_counters"

    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete=_CASCADE), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)  # YYYY-MM
    runs: Mapped[int] = mapped_column(Integer, default=0)
    upload_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_ulid)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete=_CASCADE))
    url: Mapped[str] = mapped_column(Text)
    secret_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 sekretu podpisu (do weryfikacji, nie do podpisu)
    events: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("org_id", "url", name="uq_webhooks_org_url"), Index("ix_webhooks_org_id", "org_id"))
