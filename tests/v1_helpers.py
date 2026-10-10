"""Pomocnicy testow API v1: zasiewanie organizacji/kluczy/uzytkownikow w bazie control plane i logowanie."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.control import models, service
from app.control import scopes as sc
from app.control.db import create_engine, make_sessionmaker
from app.control.security import new_csrf_token, new_session_id, sha256_hex, sign_session_id
from app.control.types import utcnow
from app.core.config import Settings

API = "/api/v1"


def run(coro):
    return asyncio.run(coro)


async def _with_db(settings: Settings, fn):
    engine = create_engine(settings)
    try:
        async with make_sessionmaker(engine)() as db:
            result = await fn(db)
            await db.commit()
            return result
    finally:
        await engine.dispose()


def with_db(settings: Settings, fn):
    """Wykonuje `fn(db)` na bazie control plane (osobna petla; commit na koncu)."""
    return run(_with_db(settings, fn))


@dataclass
class Seeded:
    org_id: str
    user_id: str
    email: str
    api_key: str
    key_id: str


def seed_org(settings: Settings, name: str = "Acme", owner_email: str = "owner@example.test",
             scopes: list[str] | None = None) -> Seeded:
    async def go(db):
        user, _ = await service.get_or_create_user(db, owner_email)
        org = await service.create_org(db, name=name, owner=user, dpa_version="v1")
        key, raw = await service.issue_api_key(
            db, org_id=org.id, name="seed", scopes=scopes or list(sc.KEY_SCOPES), created_by=user.id)
        return Seeded(org.id, user.id, user.email, raw, key.id)

    return with_db(settings, go)


def seed_key(settings: Settings, org_id: str, *, scopes: list[str] | None = None, name: str = "k",
             expires_in: timedelta | None = None, revoked: bool = False) -> tuple[str, str]:
    """Zwraca `(key_id, klucz_jawny)`."""
    async def go(db):
        key, raw = await service.issue_api_key(
            db, org_id=org_id, name=name, scopes=scopes if scopes is not None else list(sc.KEY_SCOPES),
            expires_at=utcnow() + expires_in if expires_in else None)
        if revoked:
            key.revoked_at = utcnow()
        return key.id, raw

    return with_db(settings, go)


def seed_member(settings: Settings, org_id: str, email: str, role: str) -> str:
    async def go(db):
        user, _ = await service.get_or_create_user(db, email)
        existing = await db.get(models.Membership, (org_id, user.id))
        if existing is None:
            db.add(models.Membership(org_id=org_id, user_id=user.id, role=role))
        else:
            existing.role = role
        return user.id

    return with_db(settings, go)


def seed_session(settings: Settings, user_id: str, *, ttl: timedelta = timedelta(hours=1)) -> tuple[str, str]:
    """Sesja utworzona wprost w bazie. Zwraca `(wartosc cookie daas_session, csrf)`."""
    async def go(db):
        session = models.Session(id=new_session_id(), user_id=user_id, csrf_token=new_csrf_token(),
                                 expires_at=utcnow() + ttl)
        db.add(session)
        return sign_session_id(session.id, settings.daas_secret_key or ""), session.csrf_token

    return with_db(settings, go)


def user_headers(settings: Settings, user_id: str) -> dict[str, str]:
    """Naglowki zadania w imieniu uzytkownika: cookie sesji + CSRF (cookie i naglowek). Jeden `TestClient`
    obsluguje wielu uzytkownikow bez wspoldzielenia jara cookie."""
    cookie, csrf = seed_session(settings, user_id)
    return {"Cookie": f"daas_session={cookie}; daas_csrf={csrf}", "X-CSRF-Token": csrf}


def key_headers(raw: str) -> dict[str, str]:
    return {"X-API-Key": raw}


def get_rows(settings: Settings, model, *where):
    async def go(db):
        return list((await db.execute(select(model).where(*where))).scalars())

    return with_db(settings, go)


def update_rows(settings: Settings, model, values: dict, *where) -> None:
    async def go(db):
        await db.execute(update(model).where(*where).values(**values))

    with_db(settings, go)


def outbox(settings: Settings, email: str | None = None) -> list[dict]:
    files = sorted(Path(settings.outbox_dir).glob("*.json")) if Path(settings.outbox_dir).exists() else []
    mails = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    return [m for m in mails if email is None or m["to"] == email.lower()]


def token_from_outbox(settings: Settings, email: str) -> str:
    link = outbox(settings, email)[-1]["link"]
    return link.split("token=", 1)[1]


def login_via_magic_link(client: TestClient, settings: Settings, email: str) -> dict:
    """Pelny przeplyw: prosba o link -> token z outboxa -> weryfikacja. Zwraca JSON odpowiedzi verify."""
    assert client.post(f"{API}/auth/magic-link", json={"email": email}).status_code == 202
    r = client.post(f"{API}/auth/magic-link/verify", json={"token": token_from_outbox(settings, email)})
    assert r.status_code == 200, r.text
    return r.json()


def csrf_header(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("daas_csrf")}


def digest(value: str) -> str:
    return sha256_hex(value)


def seed_job_row(settings: Settings, org_id: str, *, pipeline: str = "cs2_demo", status: str = "queued",
                 upload_id: str | None = None, force_mock: bool = True) -> str:
    """Wiersz `Job` wprost w bazie (bez zgloszenia do executora). Zwraca id."""
    from app.control.ids import new_ulid

    job_id = new_ulid()

    async def go(db):
        db.add(models.Job(id=job_id, org_id=org_id, pipeline=pipeline, status=status, upload_id=upload_id,
                          force_mock=force_mock))

    with_db(settings, go)
    return job_id
