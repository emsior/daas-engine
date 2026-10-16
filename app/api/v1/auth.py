"""Logowanie bez hasel: magic link -> sesja cookie (HttpOnly, Secure, SameSite=Lax) + CSRF double-submit."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError
from app.api.v1.deps import CSRF_COOKIE, SESSION_COOKIE, Principal, get_principal
from app.api.v1.schemas import (
    MagicLinkAccepted,
    MagicLinkConfirm,
    MagicLinkRequest,
    MembershipOut,
    MeOut,
    PrincipalOut,
    SessionOut,
    UserOut,
)
from app.control import service
from app.control.audit import record_event
from app.control.db import get_db
from app.control.mailer import magic_link_mail
from app.control.models import MagicLink, Org, Session
from app.control.security import (
    new_csrf_token,
    new_session_id,
    new_token,
    safe_equal,
    sha256_hex,
    sign_session_id,
)
from app.control.types import utcnow
from app.core.config import Settings
from app.core.ratelimit import client_ip, throttle_auth_failure, too_many

log = logging.getLogger(__name__)

public_router = APIRouter(tags=["auth"])
router = APIRouter(tags=["auth"])


ML_COOKIE = "daas_ml"
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


def ml_cookie_name(settings: Settings) -> str:
    """`__Host-daas_ml` (wymaga Secure, Path=/, bez Domain); bez TLS (lokalny dev) zwykla nazwa — jak cookie sesji."""
    return f"__Host-{ML_COOKIE}" if settings.daas_cookie_secure else ML_COOKIE


def mask_email(email: str) -> str:
    """`jan.kowalski@firma.pl` -> `j***@firma.pl` (wskazowka dla osoby, ktora widzi link, nie pelny adres)."""
    local, _, domain = email.lower().partition("@")
    return f"{local[:1]}***@{domain}"


def _email_hash(email: str) -> str:
    """Skrot adresu do meta audytu (ten sam co przy `member.invited`, pozwala skorelowac zaproszenie z dolaczeniem);
    nigdy pelny adres w audycie."""
    return hashlib.sha256(email.encode()).hexdigest()[:16]


def set_session_cookies(response: Response, settings: Settings, session: Session) -> None:
    """Cookie sesji (podpisane, HttpOnly) i cookie CSRF (czytelne dla JS; wartosc = token sesji)."""
    max_age = int(timedelta(hours=settings.daas_session_ttl_hours).total_seconds())
    common = {"max_age": max_age, "path": "/", "secure": settings.daas_cookie_secure, "samesite": "lax"}
    response.set_cookie(SESSION_COOKIE, sign_session_id(session.id, settings.daas_secret_key or ""),
                        httponly=True, **common)
    response.set_cookie(CSRF_COOKIE, session.csrf_token, httponly=False, **common)


def clear_session_cookies(response: Response, settings: Settings) -> None:
    for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(name, path="/", secure=settings.daas_cookie_secure, httponly=httponly,
                               samesite="lax")


async def _memberships_out(db: AsyncSession, user_id: str) -> list[MembershipOut]:
    return [MembershipOut(org_id=o.id, org_name=o.name, org_slug=o.slug, role=m.role)
            for m, o in await service.memberships_of(db, user_id)]


@public_router.post(
    "/auth/magic-link", status_code=202, response_model=MagicLinkAccepted,
    summary="Request a sign-in link",
    description="Sends a single-use sign-in link (valid 15 minutes) to the e-mail address. The answer is the same "
                "for known and unknown addresses. Limited per e-mail and per IP.",
)
async def request_magic_link(body: MagicLinkRequest, request: Request, response: Response,
                             db: AsyncSession = Depends(get_db)) -> MagicLinkAccepted:
    state = request.app.state
    settings: Settings = state.settings
    if not settings.has_secret_key:
        raise ProblemError(503, "sessions_unavailable", "sign-in by e-mail is not configured on this server")
    limiter = state.limiter
    email_key = hashlib.sha256(body.email.encode("utf-8")).hexdigest()[:16]
    for rule, key, per_min in (
        ("magic_ip", client_ip(request), settings.daas_rate_limit_magic_ip_per_min),
        ("magic_email", email_key, settings.daas_rate_limit_magic_email_per_min),
    ):
        wait = limiter.hit(rule, key, per_min)
        if wait > 0:
            raise too_many(wait)

    now = utcnow()
    token, digest = new_token()
    # nonce przegladarki: ta sama przegladarka zachowuje go miedzy prosbami (starsze linki dalej dzialaja)
    cookie_name = ml_cookie_name(settings)
    nonce = request.cookies.get(cookie_name, "")
    if not _NONCE_RE.match(nonce):
        nonce = new_csrf_token()
    ttl = timedelta(minutes=settings.daas_magic_link_ttl_minutes)
    await db.execute(delete(MagicLink).where(MagicLink.expires_at < now - timedelta(days=1)))
    db.add(MagicLink(token_hash=digest, email=body.email, expires_at=now + ttl, nonce_hash=sha256_hex(nonce)))
    await db.commit()
    response.set_cookie(cookie_name, nonce, max_age=int(ttl.total_seconds()), path="/",
                        secure=settings.daas_cookie_secure, httponly=True, samesite="lax")
    try:
        await run_in_threadpool(state.mailer.send, magic_link_mail(settings, body.email, token, body.locale))
    except Exception:  # noqa: BLE001
        # odpowiedz jest taka sama niezaleznie od wyniku wysylki (brak wyroczni); szczegoly w logu
        log.exception("magic link e-mail failed")
    return MagicLinkAccepted(expires_in_minutes=settings.daas_magic_link_ttl_minutes)


@public_router.post(
    "/auth/magic-link/verify", response_model=SessionOut, summary="Exchange a sign-in link for a session",
    description="Consumes the token (single use) and starts a session: sets `daas_session` (HttpOnly) and "
                "`daas_csrf`. The first sign-in creates the user. Pending invitations for the address are accepted.\n\n"
                "Login-CSRF protection: `POST /auth/magic-link` sets an HttpOnly cookie (`__Host-daas_ml`) in the "
                "browser that asked for the link. If the cookie does not match the link (another browser, a cleared "
                "cookie, a link someone else planted), the answer is `409 confirm_required` with a masked "
                "`email_hint` and the token is NOT consumed; repeat the request with `confirm: true` after the user "
                "explicitly agrees to sign in as that address in this browser.",
    responses={401: {"description": "Token unknown, expired or already used"},
               409: {"description": "`confirm_required`: link was requested from another browser"}},
)
async def verify_magic_link(body: MagicLinkConfirm, request: Request, response: Response,
                            db: AsyncSession = Depends(get_db)) -> SessionOut:
    state = request.app.state
    settings: Settings = state.settings
    if not settings.has_secret_key:
        raise ProblemError(503, "sessions_unavailable", "sign-in by e-mail is not configured on this server")
    now = utcnow()
    digest = sha256_hex(body.token)
    pending = (await db.execute(select(MagicLink).where(
        MagicLink.token_hash == digest, MagicLink.used_at.is_(None), MagicLink.expires_at > now))).scalar_one_or_none()
    if pending is None:
        throttle_auth_failure(request)
        raise ProblemError(401, "invalid_token", "the sign-in link is invalid, expired or already used")
    presented = request.cookies.get(ml_cookie_name(settings), "")
    same_browser = bool(pending.nonce_hash and presented and safe_equal(sha256_hex(presented), pending.nonce_hash))
    if not same_browser and not body.confirm:
        # token zostaje nieskonsumowany: prawowity wlasciciel (inna przegladarka) moze go jeszcze uzyc
        raise ProblemError(409, "confirm_required",
                           "this sign-in link was requested from another browser; confirm to sign in here",
                           extra={"email_hint": mask_email(pending.email)})
    # jednorazowosc: konsumpcja jest pojedynczym UPDATE-em warunkowym (dwa rownolegle zadania: wygra jedno)
    consumed = await db.execute(update(MagicLink).where(
        MagicLink.token_hash == digest, MagicLink.used_at.is_(None), MagicLink.expires_at > now,
    ).values(used_at=now))
    if consumed.rowcount != 1:
        await db.rollback()
        throttle_auth_failure(request)
        raise ProblemError(401, "invalid_token", "the sign-in link is invalid, expired or already used")
    link = await db.get(MagicLink, digest)
    user, _created = await service.get_or_create_user(db, link.email)
    added = await service.accept_pending_invitations(db, user)
    memberships = await service.memberships_of(db, user.id)
    session = Session(
        id=new_session_id(), user_id=user.id, org_id=memberships[0][1].id if memberships else None,
        csrf_token=new_csrf_token(), expires_at=now + timedelta(hours=settings.daas_session_ttl_hours))
    db.add(session)
    for org_id, role in added:
        await record_event(db, request, org_id=org_id, actor_type="user", actor_id=user.id,
                           action="member.added", target=f"user:{user.id}",
                           meta={"role": role, "email_hash": _email_hash(user.email)})
    await record_event(db, request, org_id=session.org_id, actor_type="user", actor_id=user.id,
                       action="auth.login")
    await db.commit()
    set_session_cookies(response, settings, session)
    response.delete_cookie(ml_cookie_name(settings), path="/", secure=settings.daas_cookie_secure, httponly=True,
                           samesite="lax")
    return SessionOut(
        user=UserOut.model_validate(user),
        orgs=[MembershipOut(org_id=o.id, org_name=o.name, org_slug=o.slug, role=m.role) for m, o in memberships],
        csrf_token=session.csrf_token, expires_at=session.expires_at)


@router.post("/auth/logout", status_code=204, summary="End the session",
             description="Deletes the server-side session and clears the cookies. Requires the CSRF header.")
async def logout(request: Request, principal: Principal = Depends(get_principal),
                 db: AsyncSession = Depends(get_db)) -> Response:
    if principal.session is None:
        raise ProblemError(403, "user_session_required", "this operation requires a signed-in user")
    await record_event(db, request, org_id=principal.session.org_id, actor_type="user",
                       actor_id=principal.user.id, action="auth.logout")
    await db.execute(delete(Session).where(Session.id == principal.session.id))
    await db.commit()
    out = Response(status_code=204)
    clear_session_cookies(out, request.app.state.settings)
    return out


@router.get("/me", response_model=MeOut, summary="Who am I",
            description="The caller, their organizations and roles. For an API key: the key's organization and scopes.")
async def me(request: Request, principal: Principal = Depends(get_principal),
             db: AsyncSession = Depends(get_db)) -> MeOut:
    if principal.user is not None:
        added = await service.accept_pending_invitations(db, principal.user)
        for org_id, role in added:
            await record_event(db, request, org_id=org_id, actor_type="user", actor_id=principal.user.id,
                               action="member.added", target=f"user:{principal.user.id}",
                               meta={"role": role, "email_hash": _email_hash(principal.user.email)})
        if added:
            await db.commit()
        return MeOut(principal=PrincipalOut(type="user"), user=UserOut.model_validate(principal.user),
                     orgs=await _memberships_out(db, principal.user.id))
    org = (await db.execute(select(Org).where(Org.id == principal.org_id))).scalar_one_or_none()
    orgs = [MembershipOut(org_id=org.id, org_name=org.name, org_slug=org.slug)] if org else []
    return MeOut(principal=PrincipalOut(type="api_key", org_id=principal.org_id, scopes=sorted(principal.scopes)),
                 orgs=orgs)
