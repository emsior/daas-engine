"""OAuth 2.1 dla konektorow MCP (ChatGPT, Claude, ...): serwer autoryzacji z SDK `mcp` + trwalosc w control plane.

Podzial pracy (SDK `mcp` 2.3, pakiet `mcp.server.auth`):
    * SDK: trasy `/authorize`, `/token`, `/register`, `/revoke`, metadane RFC 8414 (`/.well-known/oauth-authorization-
      server`) i RFC 9728 (`/.well-known/oauth-protected-resource/mcp`), walidacja zadan, PKCE S256 (porownanie
      `code_verifier` z `code_challenge`), zgodnosc `redirect_uri`, uwierzytelnienie klienta, format odpowiedzi tokena,
    * ten modul: trwalosc (`oauth_clients`, `oauth_codes`, `oauth_tokens`; kody i tokeny wylacznie jako SHA-256),
      zasady rejestracji, zgoda uzytkownika w kokpicie (`/oauth/consent?request=<id>`, decyzja w `app/api/v1/oauth.py`),
      rotacja i cofanie tokenow, audyt, limity tras SDK (`OAuthRouteGuard`), sprzatanie (`purge_expired`).

Przeplyw: klient rejestruje sie (`/register`, RFC 7591) -> `/authorize` zapisuje zadanie `pending` i odsyla przegladarke
do kokpitu -> uzytkownik wybiera organizacje i zakresy (`POST /api/v1/oauth/requests/{id}/decision`), wiersz dostaje
`org_id`, `user_id`, skrot kodu i status `approved` (kod zyje 60 s) -> `/token` wymienia kod na pare tokenow
(`daas_oat_...` dostepu, `daas_ort_...` odswiezania) -> token dostepu dziala w `/mcp` jak klucz API organizacji zgody
(`principal_for`), z zakresami zgody obcietymi do biezacej roli uzytkownika.

Lancuch `parent_id` (bez nowych kolumn): token dostepu -> token odswiezania wydany razem z nim; token odswiezania ->
poprzedni token odswiezania (rotacja) albo id wiersza `oauth_codes`, z ktorego powstala pierwsza para. Dzieki temu
"wszystkie tokeny wydane z kodu" (ponowne uzycie kodu, RFC 6749 4.1.2) i "polaczenie z aplikacja" (odlaczenie
w kokpicie) to poddrzewo od jednego korzenia (`revoke_subtree`).
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import secrets
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote_plus, urlencode

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.routes import (
    REGISTRATION_PATH,
    REVOCATION_PATH,
    build_metadata,
    build_resource_metadata_url,
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull
from mcp.shared.auth import OAuthToken as TokenResponse
from pydantic import AnyHttpUrl, AnyUrl, ConfigDict, TypeAdapter, ValidationError
from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.problems import PROBLEM_MEDIA_TYPE, problem_body
from app.control import scopes as sc
from app.control.audit import record_event
from app.control.ids import new_ulid
from app.control.models import Membership, OAuthClient, OAuthCode, OAuthToken, User
from app.control.security import sha256_hex
from app.control.types import utcnow
from app.core.config import Settings
from app.core.observability import get_request_id
from app.core.ratelimit import client_ip, too_many

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.api.v1.deps import Principal

log = logging.getLogger(__name__)

#: Prefiksy tokenow: brama `/mcp` rozpoznaje token OAuth po prefiksie (bez zapytania do bazy). Sekret = 32 losowe bajty.
ACCESS_PREFIX = "daas_oat_"
REFRESH_PREFIX = "daas_ort_"
#: Kod autoryzacyjny jest wazny tyle od zgody w kokpicie (krotko: klient wymienia go od razu po przekierowaniu).
CODE_TTL = timedelta(seconds=60)
#: Zakresy klienta zarejestrowanego bez `scope` (RFC 7591) i zadania `/authorize` bez `scope`: tylko odczyt.
DEFAULT_SCOPES: tuple[str, ...] = (sc.RUNS_READ, sc.REPORTS_READ, sc.USAGE_READ)
#: Dynamiczne rejestracje klientow na minute per adres IP (niezaleznie od limitu `ip` wszystkich tras).
REGISTER_PER_MIN = 10
#: Sprzatanie (`purge_expired`): kody po dniu od wygasniecia, tokeny po 30 dniach (slad dla list i audytu), klienci
#: bez zadnego kodu ani tokenu po dobie od rejestracji (rejestracja `/register` jest anonimowa: bez sprzatania tabela
#: `oauth_clients` roslaby bez konca, do 16 KiB na wiersz).
CODE_RETENTION = timedelta(days=1)
TOKEN_RETENTION = timedelta(days=30)
CLIENT_RETENTION = timedelta(days=1)
#: `last_used_at` tokenu dostepu jest zapisywane najwyzej raz na tyle czasu (jak `LAST_USED_THROTTLE` kluczy API).
LAST_USED_THROTTLE = timedelta(minutes=5)
#: Nazwa zasobu w metadanych RFC 9728.
RESOURCE_NAME = "DaaS Engine MCP"
METADATA_PATH = "/.well-known/oauth-authorization-server"

MAX_REDIRECT_URIS = 10
MAX_TEXT = 512  # kolumny `redirect_uri`, `state`, `resource` w `oauth_codes`
MAX_CLIENT_NAME = 200  # kolumna `oauth_clients.client_name`
MAX_CLIENT_INFO_BYTES = 16 * 1024  # metadane rejestracji w `oauth_clients.info` (JSON)
MAX_REVOKE_BODY = 64 * 1024
_MAX_SECRET_LEN = 256
#: Metody uwierzytelnienia klienta w `/token` i `/revoke`: `none` = klient publiczny (PKCE), reszta = sekret z rejestracji.
AUTH_METHODS: tuple[str, ...] = ("none", "client_secret_post", "client_secret_basic")
_GRANTS = frozenset({"authorization_code", "refresh_token"})
_DEV_LOOPBACK = frozenset({"localhost", "127.0.0.1"})
_PKCE_S256 = re.compile(r"^[A-Za-z0-9_-]{43}$")
#: URL bez dopisywania `/` do pustej sciezki: issuer w metadanych porownuje sie jako napis (RFC 8414 3.3).
_URL = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True))
#: Adres klienta zadania trasy SDK (ustawia `OAuthRouteGuard`): SDK nie przekazuje providerowi obiektu `Request`.
_CLIENT_IP: contextvars.ContextVar[str | None] = contextvars.ContextVar("daas_oauth_client_ip", default=None)


# ============================================================================ modele SDK z polami DaaS
class DaasAuthorizationCode(AuthorizationCode):
    """Kod po zgodzie: `request_id` = wiersz `oauth_codes`, `org_id` = organizacja zgody, `subject` = uzytkownik."""

    request_id: str
    org_id: str


class DaasRefreshToken(RefreshToken):
    token_id: str
    org_id: str


class DaasAccessToken(AccessToken):
    token_id: str
    org_id: str


def issuer_of(settings: Settings) -> str | None:
    """Issuer (publiczny origin wdrozenia): `DAAS_OAUTH_ISSUER_URL`, a bez niego `DAAS_COCKPIT_ORIGIN`."""
    raw = (settings.daas_oauth_issuer_url or settings.daas_cockpit_origin or "").strip().rstrip("/")
    return raw or None


def grantable_scopes(requested: Iterable[str], role: str | None) -> list[str]:
    """Zakresy z zadania, ktore rola moze udzielic (kolejnosc zadania): member nie udzieli np. `org:manage`."""
    allowed = sc.ROLE_SCOPES.get(role or "", frozenset())
    return [s for s in requested if s in allowed]


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _epoch(value: datetime | None) -> int | None:
    return int(value.timestamp()) if value is not None else None


def _new_secret(prefix: str) -> tuple[str, str]:
    raw = prefix + secrets.token_urlsafe(32)
    return raw, sha256_hex(raw)


# ============================================================================ cofanie poddrzewa tokenow
async def revoke_subtree(db: AsyncSession, client_id: str, root_ids: Iterable[str], now: datetime) -> int:
    """Cofa tokeny o id z `root_ids` oraz wszystkich ich potomkow po `parent_id` (korzeniem moze byc tez id wiersza
    `oauth_codes`). Poddrzewo wyznacza rekurencyjne CTE po `parent_id` (SQLite i PostgreSQL), a do pamieci trafiaja
    tylko tokeny jeszcze niecofniete — koszt zalezy od wielkosci polaczenia, nie od liczby wszystkich tokenow klienta
    (rotacja odswiezania zostawia cofniete wiersze do czasu `purge_expired`). Zwraca liczbe cofnietych tokenow.
    Commit robi wolajacy."""
    roots = [rid for rid in dict.fromkeys(root_ids) if rid]
    if not roots:
        return 0
    tok = OAuthToken.__table__
    seed = select(tok.c.id).where(tok.c.client_id == client_id, or_(tok.c.id.in_(roots), tok.c.parent_id.in_(roots)))
    subtree = seed.cte("oauth_subtree", recursive=True)
    subtree = subtree.union_all(select(tok.c.id).where(tok.c.client_id == client_id,
                                                        tok.c.parent_id == subtree.c.id))
    rows = (await db.execute(select(OAuthToken).where(OAuthToken.id.in_(select(subtree.c.id)),
                                                      OAuthToken.revoked_at.is_(None)))).scalars().all()
    for token in rows:
        token.revoked_at = now
    return len(rows)


async def purge_expired(db: AsyncSession, now: datetime | None = None) -> tuple[int, int, int]:
    """Usuwa kody/zadania autoryzacji wygasle ponad dobe temu, tokeny wygasle ponad 30 dni temu i klientow
    zarejestrowanych ponad dobe temu, ktorzy nie maja juz zadnego kodu ani tokenu (rejestracja porzucona albo
    polaczenie, ktorego wszystkie tokeny zostaly juz sprzatniete; klient rejestruje sie wtedy od nowa, RFC 7591).
    Zwraca `(kody, tokeny, klienci)`. Commit robi wolajacy (petla retencji, `app.control.retention`)."""
    now = now or utcnow()
    codes = await db.execute(delete(OAuthCode).where(OAuthCode.expires_at < now - CODE_RETENTION))
    tokens = await db.execute(delete(OAuthToken).where(OAuthToken.expires_at.is_not(None),
                                                       OAuthToken.expires_at < now - TOKEN_RETENTION))
    has_code = exists().where(OAuthCode.client_id == OAuthClient.client_id)
    has_token = exists().where(OAuthToken.client_id == OAuthClient.client_id)
    clients = await db.execute(delete(OAuthClient).where(OAuthClient.created_at < now - CLIENT_RETENTION,
                                                         ~has_code, ~has_token))
    return codes.rowcount or 0, tokens.rowcount or 0, clients.rowcount or 0


# ============================================================================ provider (protokol SDK)
class DaasOAuthProvider(OAuthAuthorizationServerProvider[DaasAuthorizationCode, DaasRefreshToken, DaasAccessToken]):
    """`OAuthAuthorizationServerProvider` SDK `mcp` na control plane DaaS.

    `sessionmaker`: `async_sessionmaker` albo bezargumentowa funkcja, ktora go zwraca (aplikacja tworzy silnik bazy
    dopiero w lifespanie, a trasy SDK powstaja wczesniej, w `create_app`). Kazda metoda ma wlasna sesje i commit.
    `exchange_identity_assertion` (SEP-990) zostaje domyslne z protokolu: `unsupported_grant_type`."""

    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
                 | Callable[[], async_sessionmaker[AsyncSession]], *, issuer: str | None = None) -> None:
        self.settings = settings
        self._sessionmaker = sessionmaker
        resolved = (issuer or issuer_of(settings) or "").rstrip("/")
        if not resolved:
            raise ValueError("OAuth issuer is not configured")
        self.issuer = resolved
        #: Ekran zgody jest w kokpicie; bez DAAS_COCKPIT_ORIGIN kokpit i API dziela origin issuera (jeden Caddy).
        self.consent_origin = (settings.daas_cockpit_origin or resolved).strip().rstrip("/")
        self.resource_url = f"{resolved}/mcp"
        self.resource_metadata_url = str(build_resource_metadata_url(_URL.validate_python(self.resource_url)))
        self.dev = settings.app_env.strip().lower() == "dev"
        self.access_ttl = timedelta(seconds=settings.daas_oauth_access_token_ttl_sec)
        self.refresh_ttl = timedelta(seconds=settings.daas_oauth_refresh_token_ttl_sec)
        self.authorization_ttl = timedelta(seconds=settings.daas_oauth_authorization_ttl_sec)

    def _session(self) -> AsyncSession:
        maker = self._sessionmaker
        if not isinstance(maker, async_sessionmaker):
            maker = maker()
        return maker()

    async def _audit(self, db: AsyncSession, *, org_id: str | None, actor_type: str, actor_id: str | None,
                     action: str, client_id: str, meta: dict[str, Any]) -> None:
        await record_event(db, None, org_id=org_id, actor_type=actor_type, actor_id=actor_id, action=action,
                           target=f"oauth:{client_id}", meta={"client_id": client_id, **meta}, ip=_CLIENT_IP.get(),
                           settings=self.settings)

    # ---------------------------------------------------------------- klienci (RFC 7591)
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if not client_id or len(client_id) > 64:
            return None
        async with self._session() as db:
            row = await db.get(OAuthClient, client_id)
        if row is None or row.revoked_at is not None or (row.expires_at is not None and row.expires_at <= utcnow()):
            return None
        try:
            return OAuthClientInformationFull.model_validate(row.info)
        except ValidationError:
            log.warning("oauth client record is not valid client metadata client_id=%s", client_id)
            return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._check_registration(client_info)
        info = client_info.model_dump(mode="json")
        if len(json.dumps(info)) > MAX_CLIENT_INFO_BYTES:
            raise RegistrationError("invalid_client_metadata", "client metadata is too large")
        async with self._session() as db:
            db.add(OAuthClient(client_id=client_info.client_id, client_name=client_info.client_name,
                               redirect_uris=[str(u) for u in client_info.redirect_uris or ()], info=info))
            # rejestracja jest anonimowa i nie nalezy do organizacji: zdarzenie bez org_id (kolumna dopuszcza NULL)
            await self._audit(db, org_id=None, actor_type="system", actor_id=None, action="oauth.client_registered",
                              client_id=client_info.client_id, meta={})
            await db.commit()

    def _check_registration(self, info: OAuthClientInformationFull) -> None:
        """Zasady DaaS ponad walidacje SDK. Komunikaty nie powtarzaja wartosci przyslanych przez klienta."""
        if (info.token_endpoint_auth_method or "client_secret_post") not in AUTH_METHODS:
            raise RegistrationError("invalid_client_metadata",
                                    "token_endpoint_auth_method must be none, client_secret_post or client_secret_basic")
        if not set(info.grant_types) <= _GRANTS:
            raise RegistrationError("invalid_client_metadata",
                                    "grant_types may contain only authorization_code and refresh_token")
        if not set(info.response_types) <= {"code"}:
            raise RegistrationError("invalid_client_metadata", "response_types may contain only code")
        if info.client_name is not None and len(info.client_name) > MAX_CLIENT_NAME:
            raise RegistrationError("invalid_client_metadata",
                                    f"client_name must be at most {MAX_CLIENT_NAME} characters")
        uris = info.redirect_uris or []
        if not uris or len(uris) > MAX_REDIRECT_URIS:
            raise RegistrationError("invalid_redirect_uri", f"between 1 and {MAX_REDIRECT_URIS} redirect_uris are required")
        for uri in uris:
            self._check_redirect_uri(uri)

    def _check_redirect_uri(self, uri: AnyUrl) -> None:
        if len(str(uri)) > MAX_TEXT:
            raise RegistrationError("invalid_redirect_uri", f"a redirect URI must be at most {MAX_TEXT} characters")
        if uri.fragment is not None:
            raise RegistrationError("invalid_redirect_uri", "a redirect URI must not contain a fragment")
        if uri.username is not None or uri.password is not None:
            raise RegistrationError("invalid_redirect_uri", "a redirect URI must not contain credentials")
        if uri.scheme == "https" and uri.host:
            return
        if self.dev and uri.scheme == "http" and uri.host in _DEV_LOOPBACK:
            return
        raise RegistrationError("invalid_redirect_uri", "redirect URIs must use https")

    # ---------------------------------------------------------------- /authorize -> zgoda w kokpicie
    def _default_scopes(self, client: OAuthClientInformationFull) -> list[str]:
        registered = [s for s in (client.scope or "").split() if s in sc.KEY_SCOPES]
        return _dedupe(registered) or list(DEFAULT_SCOPES)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Zapisuje zadanie `pending` i zwraca adres ekranu zgody w kokpicie. Bez `scope` w zadaniu: zakresy
        z rejestracji klienta (SDK pilnuje, by zadane zakresy byly ich podzbiorem)."""
        scopes = _dedupe(params.scopes) if params.scopes else self._default_scopes(client)
        if not scopes or any(s not in sc.KEY_SCOPES for s in scopes):
            raise AuthorizeError("invalid_scope", "requested scopes are not supported")
        if not _PKCE_S256.match(params.code_challenge):
            raise AuthorizeError("invalid_request", "code_challenge must be an S256 PKCE challenge (43 base64url "
                                                    "characters)")
        redirect_uri = str(params.redirect_uri)
        for name, value in (("redirect_uri", redirect_uri), ("state", params.state), ("resource", params.resource)):
            if value is not None and len(value) > MAX_TEXT:
                raise AuthorizeError("invalid_request", f"{name} must be at most {MAX_TEXT} characters")
        now = utcnow()
        row = OAuthCode(id=new_ulid(), client_id=client.client_id, scopes=scopes,
                        code_challenge=params.code_challenge, redirect_uri=redirect_uri,
                        redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly, state=params.state,
                        resource=params.resource, status="pending", created_at=now,
                        expires_at=now + self.authorization_ttl)
        async with self._session() as db:
            db.add(row)
            await db.commit()
        return f"{self.consent_origin}/oauth/consent?{urlencode({'request': row.id})}"

    # ---------------------------------------------------------------- /token: kod autoryzacyjny
    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> DaasAuthorizationCode | None:
        if not authorization_code or len(authorization_code) > _MAX_SECRET_LEN:
            return None
        async with self._session() as db:
            row = (await db.execute(select(OAuthCode).where(
                OAuthCode.code_hash == sha256_hex(authorization_code)))).scalar_one_or_none()
            if row is None:
                return None
            if row.status == "exchanged":
                # RFC 6749 4.1.2: kod uzyty drugi raz -> odmowa (SDK: invalid_grant) i cofniecie tokenow z tego kodu
                await self._revoke_code_family(db, row)
                await db.commit()
                return None
            if (row.status != "approved" or row.client_id != client.client_id or row.org_id is None
                    or row.user_id is None):
                return None
        # wygasly kod wraca z prawdziwym `expires_at`: SDK odpowiada `invalid_grant` ("authorization code has expired")
        return DaasAuthorizationCode(
            code=authorization_code, scopes=list(row.scopes or []), expires_at=row.expires_at.timestamp(),
            client_id=row.client_id, code_challenge=row.code_challenge, redirect_uri=AnyUrl(row.redirect_uri),
            redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly, resource=row.resource,
            subject=row.user_id, request_id=row.id, org_id=row.org_id)

    async def _revoke_code_family(self, db: AsyncSession, row: OAuthCode) -> None:
        count = await revoke_subtree(db, row.client_id, [row.id], utcnow())
        log.warning("oauth authorization code reused client_id=%s revoked=%d", row.client_id, count)
        if row.org_id is not None:
            await self._audit(db, org_id=row.org_id, actor_type="system", actor_id=None,
                              action="oauth.token_revoked", client_id=row.client_id,
                              meta={"reason": "code_replay", "count": count})

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: DaasAuthorizationCode) -> TokenResponse:
        code = authorization_code
        now = utcnow()
        async with self._session() as db:
            # jednorazowosc: warunkowy UPDATE (dwie rownolegle wymiany tego samego kodu: wygrywa jedna)
            consumed = await db.execute(update(OAuthCode).where(
                OAuthCode.id == code.request_id, OAuthCode.status == "approved", OAuthCode.expires_at > now,
            ).values(status="exchanged", exchanged_at=now))
            if consumed.rowcount != 1:
                await db.rollback()
                row = await db.get(OAuthCode, code.request_id)
                if row is not None and row.status == "exchanged":
                    await self._revoke_code_family(db, row)
                    await db.commit()
                raise TokenError("invalid_grant", "authorization code is invalid, expired or already used")
            response = await self._issue_pair(db, client_id=client.client_id, org_id=code.org_id,
                                              user_id=code.subject, grant_scopes=list(code.scopes),
                                              access_scopes=list(code.scopes), resource=code.resource,
                                              parent_id=code.request_id, now=now)
            await self._audit(db, org_id=code.org_id, actor_type="user", actor_id=code.subject,
                              action="oauth.token_issued", client_id=client.client_id,
                              meta={"grant": "authorization_code", "scopes": list(code.scopes)})
            await db.commit()
        return response

    async def _issue_pair(self, db: AsyncSession, *, client_id: str, org_id: str, user_id: str | None,
                          grant_scopes: list[str], access_scopes: list[str], resource: str | None, parent_id: str,
                          now: datetime, refresh_used_at: datetime | None = None) -> TokenResponse:
        """Nowa para: token odswiezania (zakresy calej zgody, potomek `parent_id`) i token dostepu (zakresy zadane,
        potomek tokenu odswiezania). W bazie tylko skroty SHA-256."""
        refresh_raw, refresh_hash = _new_secret(REFRESH_PREFIX)
        access_raw, access_hash = _new_secret(ACCESS_PREFIX)
        refresh = OAuthToken(id=new_ulid(), kind="refresh", token_hash=refresh_hash, client_id=client_id,
                             org_id=org_id, user_id=user_id, scopes=grant_scopes, resource=resource,
                             parent_id=parent_id, created_at=now, expires_at=now + self.refresh_ttl,
                             last_used_at=refresh_used_at)
        access = OAuthToken(id=new_ulid(), kind="access", token_hash=access_hash, client_id=client_id, org_id=org_id,
                            user_id=user_id, scopes=access_scopes, resource=resource, parent_id=refresh.id,
                            created_at=now, expires_at=now + self.access_ttl)
        db.add_all([refresh, access])
        return TokenResponse(access_token=access_raw, expires_in=int(self.access_ttl.total_seconds()),
                             scope=" ".join(access_scopes), refresh_token=refresh_raw)  # token_type: Bearer

    # ---------------------------------------------------------------- /token: odswiezanie (rotacja)
    async def _active_token(self, raw: str, kind: str) -> OAuthToken | None:
        prefix = ACCESS_PREFIX if kind == "access" else REFRESH_PREFIX
        if not raw or not raw.startswith(prefix) or len(raw) > _MAX_SECRET_LEN:
            return None
        async with self._session() as db:
            row = (await db.execute(select(OAuthToken).where(
                OAuthToken.token_hash == sha256_hex(raw), OAuthToken.kind == kind,
                OAuthToken.revoked_at.is_(None)))).scalar_one_or_none()
        if row is None or (row.expires_at is not None and row.expires_at <= utcnow()):
            return None
        return row

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str) -> DaasRefreshToken | None:
        row = await self._active_token(refresh_token, "refresh")
        if row is None or row.client_id != client.client_id or row.user_id is None:
            return None
        return DaasRefreshToken(token=refresh_token, client_id=row.client_id, scopes=list(row.scopes or []),
                                expires_at=_epoch(row.expires_at), resource=row.resource, subject=row.user_id,
                                token_id=row.id, org_id=row.org_id)

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: DaasRefreshToken,
                                     scopes: list[str]) -> TokenResponse:
        """Rotacja: stary token odswiezania i jego tokeny dostepu sa cofane, powstaje nowa para. Nowy token
        odswiezania ma zakresy calej zgody (RFC 6749 6), token dostepu - zadany podzbior."""
        granted = list(refresh_token.scopes)
        requested = _dedupe(scopes) or granted
        if not set(requested) <= set(granted):
            raise TokenError("invalid_scope", "requested scopes exceed the original grant")
        now = utcnow()
        async with self._session() as db:
            member = await db.get(Membership, (refresh_token.org_id, refresh_token.subject))
            if member is None:  # uzytkownik nie nalezy juz do organizacji: zgoda wygasa razem z czlonkostwem
                count = await revoke_subtree(db, client.client_id, [refresh_token.token_id], now)
                await self._audit(db, org_id=refresh_token.org_id, actor_type="system", actor_id=None,
                                  action="oauth.token_revoked", client_id=client.client_id,
                                  meta={"reason": "membership_ended", "count": count})
                await db.commit()
                raise TokenError("invalid_grant", "the grant is no longer valid")
            rotated = await db.execute(update(OAuthToken).where(
                OAuthToken.id == refresh_token.token_id, OAuthToken.revoked_at.is_(None)).values(revoked_at=now))
            if rotated.rowcount != 1:  # rownolegle odswiezenie tym samym tokenem: wygrywa jedno
                await db.rollback()
                raise TokenError("invalid_grant", "refresh token is invalid, expired or already used")
            await db.execute(update(OAuthToken).where(
                OAuthToken.client_id == client.client_id, OAuthToken.parent_id == refresh_token.token_id,
                OAuthToken.kind == "access", OAuthToken.revoked_at.is_(None)).values(revoked_at=now))
            response = await self._issue_pair(db, client_id=client.client_id, org_id=refresh_token.org_id,
                                              user_id=refresh_token.subject, grant_scopes=granted,
                                              access_scopes=requested, resource=refresh_token.resource,
                                              parent_id=refresh_token.token_id, now=now, refresh_used_at=now)
            await self._audit(db, org_id=refresh_token.org_id, actor_type="user", actor_id=refresh_token.subject,
                              action="oauth.token_issued", client_id=client.client_id,
                              meta={"grant": "refresh_token", "scopes": requested})
            await db.commit()
        return response

    # ---------------------------------------------------------------- tokeny dostepu, cofanie
    async def load_access_token(self, token: str) -> DaasAccessToken | None:
        row = await self._active_token(token, "access")
        if row is None:
            return None
        return DaasAccessToken(token=token, client_id=row.client_id, scopes=list(row.scopes or []),
                               expires_at=_epoch(row.expires_at), resource=row.resource, subject=row.user_id,
                               token_id=row.id, org_id=row.org_id)

    async def revoke_token(self, token: DaasAccessToken | DaasRefreshToken) -> None:
        """RFC 7009 (`/revoke`): cofa token, a token odswiezania razem z potomkami (jego tokeny dostepu)."""
        now = utcnow()
        async with self._session() as db:
            token_id = getattr(token, "token_id", None)
            row = await db.get(OAuthToken, token_id) if token_id else (await db.execute(select(OAuthToken).where(
                OAuthToken.token_hash == sha256_hex(token.token)))).scalar_one_or_none()
            if row is None:
                return
            count = await revoke_subtree(db, row.client_id, [row.id], now)
            if count:
                await self._audit(db, org_id=row.org_id, actor_type="user", actor_id=row.user_id,
                                  action="oauth.token_revoked", client_id=row.client_id,
                                  meta={"reason": "client_revoked", "count": count})
            await db.commit()

    async def principal_for(self, token: str) -> Principal | None:
        """Podmiot `/mcp` dla tokenu dostepu: organizacja zgody, zakresy zgody obciete do BIEZACEJ roli uzytkownika
        (degradacja dziala od razu, usuniecie z organizacji = 401). None = token nieznany, cofniety, wygasly, klient
        cofniety albo uzytkownik poza organizacja."""
        # import lokalny: pakiet app.api.v1 importuje ten modul (cykl przy imporcie na poziomie modulu)
        from app.api.v1.deps import Principal

        if not token.startswith(ACCESS_PREFIX) or len(token) > _MAX_SECRET_LEN:
            return None
        now = utcnow()
        async with self._session() as db:
            found = (await db.execute(
                select(OAuthToken, Membership.role, User)
                .join(OAuthClient, OAuthClient.client_id == OAuthToken.client_id)
                .join(Membership, and_(Membership.org_id == OAuthToken.org_id,
                                       Membership.user_id == OAuthToken.user_id))
                .join(User, User.id == OAuthToken.user_id)
                .where(OAuthToken.token_hash == sha256_hex(token), OAuthToken.kind == "access",
                       OAuthToken.revoked_at.is_(None), OAuthClient.revoked_at.is_(None)))).first()
            if found is None:
                return None
            row, role, user = found
            if row.expires_at is not None and row.expires_at <= now:
                return None
            if row.last_used_at is None or now - row.last_used_at > LAST_USED_THROTTLE:
                await db.execute(update(OAuthToken).where(OAuthToken.id == row.id).values(last_used_at=now))
                await db.commit()
        scopes = frozenset(grantable_scopes(row.scopes or (), role))
        return Principal(kind="oauth", org_id=row.org_id, scopes=scopes, user=user, oauth_token_id=row.id,
                         oauth_client_id=row.client_id)


# ============================================================================ trasy SDK w aplikacji
def _problem(request: Request, status: int, code: str, detail: str,
             headers: dict[str, str] | None = None) -> JSONResponse:
    body = problem_body(status, code, detail, request.url.path, get_request_id())
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


class OAuthRouteGuard:
    """Endpoint ASGI przed trasa SDK: limit `ip` (ten sam kubelek co `/api/v1` i `/mcp`), na `POST /register`
    dodatkowo `oauth_register` (`REGISTER_PER_MIN` / min / IP); przekroczenie = 429 problem+json z `Retry-After`.
    Adres klienta trafia do audytu przez `_CLIENT_IP`.

    `POST /revoke`: SDK 2.3 wymaga pola `client_secret` w formularzu takze od klienta publicznego (model
    `RevocationRequest`), a RFC 7009 go nie wymaga. Brakujace pole jest dopisywane puste — uwierzytelnienia to nie
    oslabia: klient z sekretem nadal musi go podac (`ClientAuthenticator`), klient publiczny sekretu nie ma."""

    def __init__(self, app: ASGIApp, *, path: str) -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover - trasy HTTP
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        state = request.app.state
        ip = client_ip(request)
        rules = [("ip", state.settings.daas_rate_limit_ip_per_min)]
        if self.path == REGISTRATION_PATH and request.method == "POST":
            rules.append(("oauth_register", REGISTER_PER_MIN))
        for rule, per_min in rules:
            wait = state.limiter.hit(rule, ip, per_min)
            if wait > 0:
                await _problem(request, 429, "rate_limited", "rate limit exceeded", too_many(wait).headers)(
                    scope, receive, send)
                return
        if self.path == REVOCATION_PATH and request.method == "POST":
            patched = await _revocation_with_secret_field(scope, receive)
            if patched is None:
                await _problem(request, 413, "payload_too_large",
                               f"request body exceeds {MAX_REVOKE_BODY} bytes")(scope, receive, send)
                return
            scope, receive = patched
        marker = _CLIENT_IP.set(ip)
        try:
            await self.app(scope, receive, send)
        finally:
            _CLIENT_IP.reset(marker)


async def _revocation_with_secret_field(scope: Scope, receive: Receive) -> tuple[Scope, Receive] | None:
    """Buforuje cialo `POST /revoke` (limit `MAX_REVOKE_BODY`) i dopisuje `client_secret=`, gdy formularz go nie ma.
    None = cialo za duze."""
    body = bytearray()
    trailing: list[Message] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            trailing.append(message)
            break
        body.extend(message.get("body", b""))
        if len(body) > MAX_REVOKE_BODY:
            return None
        if not message.get("more_body", False):
            break
    headers = [(k, v) for k, v in scope.get("headers", []) if k.lower() != b"content-length"]
    content_type = next((v for k, v in headers if k.lower() == b"content-type"), b"").decode("latin-1").lower()
    if content_type.startswith("application/x-www-form-urlencoded"):
        keys = {unquote_plus(part.split(b"=", 1)[0].decode("latin-1")) for part in bytes(body).split(b"&") if part}
        if "client_secret" not in keys:
            body.extend(b"&client_secret=" if body else b"client_secret=")
    headers.append((b"content-length", str(len(body)).encode("latin-1")))
    queued: list[Message] = [{"type": "http.request", "body": bytes(body), "more_body": False}, *trailing]

    async def replay() -> Message:
        return queued.pop(0) if queued else await receive()

    return {**scope, "headers": headers}, replay


def build_routes(provider: DaasOAuthProvider, issuer_url: AnyHttpUrl) -> list[Route]:
    """Trasy SDK (serwer autoryzacji + metadane zasobu `/mcp`) owiniete `OAuthRouteGuard`. Metadane RFC 8414 sa
    budowane tym samym `build_metadata` co w SDK, ale wymieniaja tez metode `none`: konektory (ChatGPT, Claude)
    rejestruja sie jako klienci publiczni z PKCE, a SDK ich obsluguje, tylko nie oglasza."""
    registration = ClientRegistrationOptions(enabled=True, valid_scopes=list(sc.KEY_SCOPES),
                                             default_scopes=list(DEFAULT_SCOPES))
    revocation = RevocationOptions(enabled=True)
    routes = create_auth_routes(provider, issuer_url=issuer_url, client_registration_options=registration,
                                revocation_options=revocation)
    routes += create_protected_resource_routes(
        resource_url=_URL.validate_python(provider.resource_url), authorization_servers=[issuer_url],
        scopes_supported=list(sc.KEY_SCOPES), resource_name=RESOURCE_NAME)
    metadata = build_metadata(issuer_url, None, registration, revocation)
    metadata.token_endpoint_auth_methods_supported = list(AUTH_METHODS)
    metadata.revocation_endpoint_auth_methods_supported = list(AUTH_METHODS)
    guarded: list[Route] = []
    for route in routes:
        endpoint: ASGIApp = route.app
        if route.path == METADATA_PATH:
            endpoint = cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"])
        guarded.append(Route(route.path, endpoint=OAuthRouteGuard(endpoint, path=route.path),
                             methods=sorted(route.methods or ()), name=route.name, include_in_schema=False))
    return guarded


def mount_oauth(app: FastAPI, settings: Settings) -> DaasOAuthProvider | None:
    """Montuje serwer autoryzacji (tylko cloud, `DAAS_OAUTH_ENABLED=true`). Zwraca provider albo None, gdy issuer
    nie jest ustawiony albo nie jest originem https (http tylko dla localhost) — wtedy OAuth jest wylaczony
    z ostrzezeniem w dzienniku, a trasy nie istnieja (404)."""
    issuer = issuer_of(settings)
    if issuer is None:
        log.warning("OAuth for MCP connectors is off: set DAAS_OAUTH_ISSUER_URL or DAAS_COCKPIT_ORIGIN")
        return None
    try:
        issuer_url = _URL.validate_python(issuer)
        if issuer_url.path not in (None, "", "/") or issuer_url.query or issuer_url.fragment is not None:
            raise ValueError("the issuer must be an origin")
        provider = DaasOAuthProvider(settings, lambda: app.state.db_sessionmaker, issuer=str(issuer_url))
        routes = build_routes(provider, issuer_url)
    except ValueError:
        log.warning("OAuth for MCP connectors is off: the issuer must be an https origin (http only for localhost)")
        return None
    app.router.routes.extend(routes)
    return provider
