"""Zgoda uzytkownika na dostep konektora MCP (OAuth 2.1) i polaczone aplikacje organizacji.

`/authorize` (SDK, `app.control.oauth`) zapisuje zadanie `pending` i odsyla przegladarke do kokpitu
(`/oauth/consent?request=<id>`). Kokpit czyta zadanie tutaj (tylko sesja uzytkownika; klucz API nie jest osoba,
wiec nie udziela zgody: 403) i wysyla decyzje; odpowiedz to adres powrotu do aplikacji (`redirect_to`) z kodem
autoryzacyjnym albo `error=access_denied`, z zachowanym `state` i istniejacym query adresu.

Polaczona aplikacja = aktywny token odswiezania organizacji (`GET/DELETE /orgs/{org_id}/oauth/grants`, zakres
`org:manage`); odlaczenie cofa go razem z potomkami (tokeny dostepu, rotacje).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Path, Request, Response
from mcp.server.auth.provider import construct_redirect_uri
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError, not_found
from app.api.v1.deps import OrgContext, org_access, session_user
from app.api.v1.schemas import RoleName
from app.control import scopes as sc
from app.control import service
from app.control.audit import record_event
from app.control.db import get_db
from app.control.models import Membership, OAuthClient, OAuthCode, OAuthToken, User
from app.control.oauth import CODE_TTL, grantable_scopes, revoke_subtree
from app.control.security import new_token
from app.control.types import utcnow

router = APIRouter(tags=["oauth"])

_MANAGE = org_access(sc.ORG_MANAGE)
RequestId = Annotated[str, Path(min_length=1, max_length=64, description="Authorization request id from the "
                                                                          "`request` query parameter of the consent page")]
ScopeName = Annotated[str, Field(min_length=1, max_length=40)]


# ----------------------------------------------------------------------------- schematy
class OAuthOrgOption(BaseModel):
    org_id: str
    name: str
    role: RoleName
    scopes_grantable: list[str] = Field(..., description="requested scopes this role can grant; others are shown "
                                                         "disabled")


class OAuthRequestOut(BaseModel):
    request_id: str
    client_id: str
    client_name: str | None = Field(default=None, description="name the application registered (self-declared)")
    scopes_requested: list[str]
    redirect_host: str = Field(..., description="host the browser returns to after the decision")
    expires_at: datetime = Field(..., description="the decision must be made before this moment (UTC)")
    organizations: list[OAuthOrgOption] = Field(..., description="organizations of the signed-in user, oldest first")


class OAuthDecisionIn(BaseModel):
    approve: bool
    org_id: str | None = Field(default=None, max_length=64,
                               description="organization the connection acts in; required to approve")
    scopes: list[ScopeName] | None = Field(default=None, max_length=20,
                                           description="scopes to grant; default: every grantable requested scope. "
                                                       "Scopes that were not requested or that the role cannot "
                                                       "grant are ignored")


class OAuthDecisionOut(BaseModel):
    redirect_to: str = Field(..., description="send the browser here: the application's redirect URI with `code` "
                                              "and `state`, or `error=access_denied`")


class OAuthGrantOut(BaseModel):
    token_id: str = Field(..., description="id to disconnect with `DELETE .../oauth/grants/{token_id}`; it changes "
                                           "when the application refreshes its tokens")
    client_id: str
    client_name: str | None = None
    user_id: str | None = Field(default=None, description="user who approved the connection")
    role: RoleName | None = Field(default=None, description="that user's current role; `null` = no longer a member")
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None


# ----------------------------------------------------------------------------- pomocnicze
def redirect_host(uri: str) -> str:
    """Host (z portem, gdy niestandardowy) adresu powrotu: tyle pokazuje ekran zgody."""
    parts = urlsplit(uri)
    host = parts.hostname or ""
    if parts.port is not None and not (parts.scheme == "https" and parts.port == 443):
        host = f"{host}:{parts.port}"
    return host


def _expired() -> ProblemError:
    return ProblemError(410, "expired", "the authorization request has expired; start again from the application")


async def _load_request(db: AsyncSession, request_id: str) -> OAuthCode:
    row = await db.get(OAuthCode, request_id)
    if row is None:
        raise not_found("authorization request")
    return row


# ----------------------------------------------------------------------------- zgoda
@router.get(
    "/oauth/requests/{request_id}", response_model=OAuthRequestOut, summary="Read an authorization request",
    description="Consent screen data for an application (ChatGPT, Claude, ...) that asked for access through "
                "OAuth: its name, the requested scopes, where the browser returns, and the signed-in user's "
                "organizations with the scopes each role can grant. Signed-in users only; an API key gets `403`.",
    responses={403: {"description": "Not a user session"},
               404: {"description": "Unknown request, or already decided"},
               410: {"description": "`expired`: the request is older than DAAS_OAUTH_AUTHORIZATION_TTL_SEC"}},
)
async def get_request(request_id: RequestId, response: Response, user: User = Depends(session_user),
                      db: AsyncSession = Depends(get_db)) -> OAuthRequestOut:
    response.headers["Cache-Control"] = "no-store"
    row = await _load_request(db, request_id)
    if row.status != "pending":
        raise not_found("authorization request")
    if row.expires_at <= utcnow():
        raise _expired()
    client = await db.get(OAuthClient, row.client_id)
    requested = list(row.scopes or [])
    orgs = [OAuthOrgOption(org_id=org.id, name=org.name, role=m.role, scopes_grantable=grantable_scopes(requested, m.role))
            for m, org in await service.memberships_of(db, user.id)]
    return OAuthRequestOut(request_id=row.id, client_id=row.client_id,
                           client_name=client.client_name if client is not None else None,
                           scopes_requested=requested, redirect_host=redirect_host(row.redirect_uri),
                           expires_at=row.expires_at, organizations=orgs)


@router.post(
    "/oauth/requests/{request_id}/decision", response_model=OAuthDecisionOut, summary="Approve or deny an application",
    description="One decision per request (CSRF header required). Deny: `redirect_to` carries "
                "`error=access_denied`. Approve: the user must be a member of `org_id`; the granted scopes are "
                "`scopes` (default: all) narrowed to what was requested and what the role allows, and `redirect_to` "
                "carries a single-use authorization code valid for 60 seconds. The connection then acts in that "
                "organization only, with those scopes, on behalf of the user.",
    responses={403: {"description": "Not a user session, or not a member of `org_id`"},
               404: {"description": "Unknown request"},
               409: {"description": "`request_already_decided`"},
               410: {"description": "`expired`"},
               422: {"description": "`org_id` missing, or `no_grantable_scopes`"}},
)
async def decide(request_id: RequestId, body: OAuthDecisionIn, request: Request, response: Response,
                 user: User = Depends(session_user), db: AsyncSession = Depends(get_db)) -> OAuthDecisionOut:
    response.headers["Cache-Control"] = "no-store"
    now = utcnow()
    row = await _load_request(db, request_id)
    if row.status != "pending":
        raise ProblemError(409, "request_already_decided", "this authorization request was already decided")
    if row.expires_at <= now:
        raise _expired()
    pending = and_(OAuthCode.id == row.id, OAuthCode.status == "pending", OAuthCode.expires_at > now)
    redirect_uri, state, client_id = row.redirect_uri, row.state, row.client_id

    if not body.approve:
        denied = await db.execute(update(OAuthCode).where(pending).values(status="denied", user_id=user.id))
        if denied.rowcount != 1:
            await db.rollback()
            raise ProblemError(409, "request_already_decided", "this authorization request was already decided")
        await db.commit()
        return OAuthDecisionOut(redirect_to=construct_redirect_uri(redirect_uri, error="access_denied", state=state))

    if not body.org_id:
        raise ProblemError(422, "validation_error", "request validation failed", extra={"errors": [
            {"loc": ["body", "org_id"], "msg": "org_id is required to approve", "type": "missing"}]})
    membership = await db.get(Membership, (body.org_id, user.id))
    if membership is None:
        raise ProblemError(403, "forbidden", "you are not a member of this organization")
    grantable = grantable_scopes(row.scopes or [], membership.role)
    wanted = set(body.scopes) if body.scopes is not None else set(grantable)
    granted = [s for s in grantable if s in wanted]
    if not granted:
        raise ProblemError(422, "no_grantable_scopes", "none of the requested scopes can be granted with your role")
    code, digest = new_token()
    approved = await db.execute(update(OAuthCode).where(pending).values(
        status="approved", code_hash=digest, org_id=body.org_id, user_id=user.id, scopes=granted,
        expires_at=now + CODE_TTL))
    if approved.rowcount != 1:
        await db.rollback()
        raise ProblemError(409, "request_already_decided", "this authorization request was already decided")
    await record_event(db, request, org_id=body.org_id, actor_type="user", actor_id=user.id, action="oauth.consent",
                       target=f"oauth:{client_id}", meta={"client_id": client_id, "scopes": granted})
    await db.commit()
    return OAuthDecisionOut(redirect_to=construct_redirect_uri(redirect_uri, code=code, state=state))


# ----------------------------------------------------------------------------- polaczone aplikacje
@router.get(
    "/orgs/{org_id}/oauth/grants", response_model=list[OAuthGrantOut], summary="List connected applications",
    description="Applications (ChatGPT, Claude, ...) connected to the organization through OAuth, newest first: "
                "who approved them, the granted scopes and the last use. Token values are never returned.",
)
async def list_grants(ctx: OrgContext = Depends(_MANAGE), db: AsyncSession = Depends(get_db)) -> list[OAuthGrantOut]:
    now = utcnow()
    rows = (await db.execute(
        select(OAuthToken, OAuthClient.client_name, Membership.role)
        .join(OAuthClient, OAuthClient.client_id == OAuthToken.client_id)
        .outerjoin(Membership, and_(Membership.org_id == OAuthToken.org_id, Membership.user_id == OAuthToken.user_id))
        .where(OAuthToken.org_id == ctx.org_id, OAuthToken.kind == "refresh", OAuthToken.revoked_at.is_(None),
               or_(OAuthToken.expires_at.is_(None), OAuthToken.expires_at > now))
        .order_by(OAuthToken.created_at.desc(), OAuthToken.id.desc()))).all()
    used: dict[str, datetime] = {}
    if rows:
        # ostatnie uzycie = najnowszy z: token odswiezania (rotacja) i jego tokeny dostepu (wywolania /mcp)
        used = {parent: last for parent, last in (await db.execute(
            select(OAuthToken.parent_id, func.max(OAuthToken.last_used_at))
            .where(OAuthToken.org_id == ctx.org_id, OAuthToken.kind == "access",
                   OAuthToken.parent_id.in_([token.id for token, _, _ in rows]))
            .group_by(OAuthToken.parent_id))).all() if last is not None}
    out = []
    for token, client_name, role in rows:
        stamps = [t for t in (token.last_used_at, used.get(token.id)) if t is not None]
        out.append(OAuthGrantOut(token_id=token.id, client_id=token.client_id, client_name=client_name,
                                 user_id=token.user_id, role=role, scopes=list(token.scopes or []),
                                 created_at=token.created_at, last_used_at=max(stamps) if stamps else None,
                                 expires_at=token.expires_at))
    return out


@router.delete(
    "/orgs/{org_id}/oauth/grants/{token_id}", status_code=204, summary="Disconnect an application",
    description="Revokes the connection at once: its refresh token, every access token issued from it and any "
                "later rotation. The application must ask for consent again. Disconnecting twice succeeds.",
    responses={404: {"description": "No such connection in this organization"}},
)
async def revoke_grant(token_id: Annotated[str, Path(max_length=64)], request: Request,
                       ctx: OrgContext = Depends(_MANAGE), db: AsyncSession = Depends(get_db)) -> Response:
    token = (await db.execute(select(OAuthToken).where(
        OAuthToken.id == token_id, OAuthToken.org_id == ctx.org_id, OAuthToken.kind == "refresh"))).scalar_one_or_none()
    if token is None:
        raise not_found("connection")
    count = await revoke_subtree(db, token.client_id, [token.id], utcnow())
    if count:
        await record_event(db, request, org_id=ctx.org_id, actor_type=ctx.principal.actor_type,
                           actor_id=ctx.principal.actor_id, action="oauth.token_revoked",
                           target=f"oauth:{token.client_id}",
                           meta={"client_id": token.client_id, "reason": "disconnected", "count": count})
    await db.commit()
    return Response(status_code=204)
