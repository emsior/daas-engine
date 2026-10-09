"""Uwierzytelnianie i autoryzacja API v1.

Dwa sposoby wejscia (klucz ma pierwszenstwo, gdy podano oba):
    * `X-API-Key`      — klucz organizacji (`daas_<prefiks>_<sekret>`) albo klucz wdrozenia (selfhost),
    * cookie sesji     — `daas_session` (podpisane HMAC) + CSRF double-submit dla mutacji.

`get_principal` odpowiada za TOZSAMOSC (401), `org_access(...)` za ORGANIZACJE i UPRAWNIENIA:
zasob obcej organizacji = 404 (nie ujawniamy istnienia), brak zakresu/roli = 403.
Wyjatek: trasy `members`/`invitations` odpowiadaja 403 takze dla obcej organizacji (`members_route=True`).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import Depends, Path, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError
from app.control import scopes as sc
from app.control.audit import record_event
from app.control.db import get_db
from app.control.models import ApiKey, Membership, Org, Session, User
from app.control.security import parse_api_key, safe_equal, unsign_session_cookie
from app.control.service import DEFAULT_ORG_ID
from app.control.types import utcnow
from app.core.ratelimit import throttle_auth_failure

log = logging.getLogger(__name__)

SESSION_COOKIE = "daas_session"
CSRF_COOKIE = "daas_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: `last_used_at` jest zapisywane najwyzej raz na tyle czasu (mniej zapisow na goracej sciezce).
LAST_USED_THROTTLE = timedelta(minutes=5)


@dataclass(frozen=True)
class Principal:
    """Kto wola. `kind`: `api_key` (klucz organizacji), `selfhost_key` (klucz z env), `user` (sesja cookie)."""

    kind: str
    org_id: str | None = None  # klucze: ich organizacja; uzytkownik: None (czlonkostwa sprawdzane per org)
    scopes: frozenset[str] = frozenset()
    user: User | None = field(default=None, repr=False)
    key: ApiKey | None = field(default=None, repr=False)
    session: Session | None = field(default=None, repr=False)

    @property
    def actor_type(self) -> str:
        return "user" if self.kind == "user" else "key"

    @property
    def actor_id(self) -> str | None:
        if self.user is not None:
            return self.user.id
        if self.key is not None:
            return self.key.id
        return "selfhost" if self.kind == "selfhost_key" else None

    @property
    def actor_tag(self) -> str | None:
        """Znacznik w kolumnach `created_by`: `user:<id>` albo `key:<id>`."""
        return f"{self.actor_type}:{self.actor_id}" if self.actor_id else None


@dataclass(frozen=True)
class OrgContext:
    org: Org
    principal: Principal
    role: str | None
    scopes: frozenset[str]

    @property
    def org_id(self) -> str:
        return self.org.id


def _unauthorized(request: Request, reason: str) -> ProblemError:
    throttle_auth_failure(request)  # po wyczerpaniu budzetu bledow z adresu: 429
    log.info("v1 auth denied reason=%s", reason)
    return ProblemError(401, "unauthorized", "authentication required",
                        headers={"WWW-Authenticate": "ApiKey"})


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


async def _authenticate_key(request: Request, db: AsyncSession, presented: str) -> Principal:
    state = request.app.state
    presented = presented.strip()
    # klucz wdrozenia (selfhost): ten sam mechanizm co w trasach legacy, porownanie skrotow w stalym czasie
    auth = state.auth
    if auth.configured and not state.settings.cloud and hmac.compare_digest(_digest(presented), auth.key_hash):
        return Principal(kind="selfhost_key", org_id=DEFAULT_ORG_ID, scopes=sc.SELFHOST_KEY_SCOPES)

    prefix = parse_api_key(presented)
    key = None
    if prefix is not None:
        key = (await db.execute(select(ApiKey).where(ApiKey.prefix == prefix))).scalar_one_or_none()
    # zawsze jedno porownanie skrotow, takze dla nieznanego prefiksu (brak roznicy czasowej "jest/nie ma")
    expected = key.key_hash if key is not None else "0" * 64
    candidate = hashlib.sha256(presented.encode("utf-8")).hexdigest()
    if not safe_equal(candidate, expected) or key is None:
        raise _unauthorized(request, "key_invalid")
    now = utcnow()
    if key.revoked_at is not None:
        await _audit_denied(db, request, key, "key_revoked")
        raise _unauthorized(request, "key_revoked")
    if key.expires_at is not None and key.expires_at <= now:
        await _audit_denied(db, request, key, "key_expired")
        raise _unauthorized(request, "key_expired")
    if key.last_used_at is None or now - key.last_used_at > LAST_USED_THROTTLE:
        await db.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
        await db.commit()
    return Principal(kind="api_key", org_id=key.org_id, scopes=frozenset(key.scopes or ()), key=key)


async def _audit_denied(db: AsyncSession, request: Request, key: ApiKey, reason: str) -> None:
    await record_event(db, request, org_id=key.org_id, actor_type="key", actor_id=key.id,
                       action="auth_denied", target=f"key:{key.prefix}", meta={"reason": reason})
    await db.commit()


async def _authenticate_session(request: Request, db: AsyncSession, cookie: str) -> Principal:
    state = request.app.state
    session_id = unsign_session_cookie(cookie, state.settings.daas_secret_key)
    if session_id is None:
        raise _unauthorized(request, "session_signature")
    session = await db.get(Session, session_id)
    if session is None or session.expires_at <= utcnow():
        raise _unauthorized(request, "session_missing_or_expired")
    user = await db.get(User, session.user_id)
    if user is None:
        raise _unauthorized(request, "session_user_gone")
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        cookie_value = request.cookies.get(CSRF_COOKIE, "")
        # double-submit: naglowek == cookie == token zapisany przy sesji
        if not (header and safe_equal(header, cookie_value) and safe_equal(header, session.csrf_token)):
            raise ProblemError(403, "csrf_failed", "CSRF token missing or invalid")
    return Principal(kind="user", user=user, session=session)


async def get_principal(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    """Zaleznosc: rozpoznaje wolajacego albo zglasza 401. Wynik jest tez w `request.state.principal`."""
    presented = request.headers.get("x-api-key")
    if presented is not None and presented.strip():
        principal = await _authenticate_key(request, db, presented)
    else:
        cookie = request.cookies.get(SESSION_COOKIE)
        if not cookie:
            raise _unauthorized(request, "missing")
        principal = await _authenticate_session(request, db, cookie)
    request.state.principal = principal
    return principal


def org_access(scope: str | None = None, *, members_route: bool = False) -> Callable[..., Awaitable[OrgContext]]:
    """Fabryka zaleznosci: dostep do organizacji z sciezki + wymagany zakres.

    Obca/nieistniejaca organizacja -> 404 (albo 403 gdy `members_route`); brak zakresu -> 403.
    """

    async def dependency(
        request: Request,
        org_id: str = Path(..., description="Identyfikator organizacji"),
        principal: Principal = Depends(get_principal),
        db: AsyncSession = Depends(get_db),
    ) -> OrgContext:
        def hidden() -> ProblemError:
            if members_route:
                return ProblemError(403, "forbidden", "insufficient permissions")
            return ProblemError(404, "not_found", "organization not found")

        role: str | None = None
        if principal.kind == "user":
            membership = (await db.execute(select(Membership).where(
                Membership.org_id == org_id, Membership.user_id == principal.user.id))).scalar_one_or_none()
            if membership is None:
                raise hidden()
            role = membership.role
            scopes = sc.ROLE_SCOPES.get(role, frozenset())
        else:
            if principal.org_id != org_id:
                raise hidden()
            scopes = principal.scopes
        org = await db.get(Org, org_id)
        if org is None:
            raise hidden()
        if scope is not None and scope not in scopes:
            await record_event(db, request, org_id=org.id, actor_type=principal.actor_type,
                               actor_id=principal.actor_id, action="auth_denied", target=request.url.path[:80],
                               meta={"reason": "insufficient_scope", "scope": scope})
            await db.commit()
            raise ProblemError(403, "insufficient_scope", "insufficient permissions",
                               extra={"required_scope": scope})
        return OrgContext(org=org, principal=principal, role=role, scopes=frozenset(scopes))

    return dependency


async def session_user(request: Request, principal: Principal = Depends(get_principal)) -> User:
    """Tylko sesja uzytkownika (np. tworzenie organizacji): klucze API nie sa osobami."""
    if principal.user is None:
        raise ProblemError(403, "user_session_required", "this operation requires a signed-in user")
    return principal.user
