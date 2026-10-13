"""Brama HTTP przed transportem MCP (`POST /mcp`): limit IP -> uwierzytelnienie -> limit per klucz -> metoda -> limit
ciala -> SDK.

Czysta aplikacja ASGI zamontowana jako trasa `/mcp` w aplikacji FastAPI, wiec obejmuja ja middleware aplikacji
(request-id, naglowki bezpieczenstwa, metryki, CORS z listy `cors_origins`). Kazda odpowiedz bramy jest
`application/problem+json` (RFC 9457) z `code` i `request_id`, jak w API v1.

Kolejnosc kontroli (zadna nie czyta ciala przed uwierzytelnieniem):
    1. limit `ip` (ten sam kubelek i prog co `/api/v1`) -> 429 + `Retry-After`,
    2. klucz z `Authorization: Bearer <klucz>` albo `X-API-Key` (ta sama weryfikacja co v1: prefiks + SHA-256,
       `revoked_at`, `expires_at`, audyt `auth_denied`) albo token dostepu OAuth `daas_oat_...` (konektory ChatGPT,
       Claude: `app.control.oauth.DaasOAuthProvider.principal_for`); cookie sesji NIE jest akceptowane (brak CSRF
       z przegladarki) -> 401 + `WWW-Authenticate: Bearer realm="daas-mcp"`, a przy wlaczonym OAuth takze
       `resource_metadata="<issuer>/.well-known/oauth-protected-resource/mcp"` (RFC 9728 5.1: po tym konektor
       znajduje serwer autoryzacji) i `error="invalid_token"` dla odrzuconego tokenu OAuth (RFC 6750 3.1),
    3. limit `mcp` per klucz (`DAAS_RATE_LIMIT_MCP_PER_MIN`; kazde zadanie, takze `tools/list`) -> 429 + `Retry-After`,
    4. tylko `POST` (tryb bezstanowy: brak strumienia GET i sesji do zamkniecia DELETE) -> 405 + `Allow: POST`,
    5. `Content-Length` i faktycznie odebrane bajty <= `DAAS_MCP_MAX_BODY_BYTES` -> 413.
Podmiot trafia do `scope["state"]["principal"]`; narzedzia czytaja go z `Request` przekazanego przez SDK
(`ctx.request_context.request.state.principal`) — bez stanu wspoldzielonego miedzy zadaniami.
"""
from __future__ import annotations

import logging
from collections import deque

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.problems import PROBLEM_MEDIA_TYPE, ProblemError, code_for, problem_body
from app.api.v1.deps import Principal, authenticate_api_key, unauthorized
from app.control.oauth import ACCESS_PREFIX
from app.core.observability import get_request_id
from app.core.ratelimit import client_ip, too_many

log = logging.getLogger(__name__)

WWW_AUTHENTICATE = 'Bearer realm="daas-mcp"'


def www_authenticate(request: Request, *, error: str | None = None) -> str:
    """Wyzwanie 401 bramy; przy wlaczonym OAuth z adresem metadanych zasobu (RFC 9728 5.1)."""
    parts = [WWW_AUTHENTICATE]
    if error:
        parts.append(f'error="{error}"')
    oauth = getattr(request.app.state, "oauth", None)
    if oauth is not None:
        parts.append(f'resource_metadata="{oauth.resource_metadata_url}"')
    return ", ".join(parts)


def presented_key(request: Request) -> str | None:
    """Klucz z `X-API-Key` (pierwszenstwo, jak w v1) albo z `Authorization: Bearer`. Cookie jest ignorowane."""
    header = request.headers.get("x-api-key")
    if header is not None and header.strip():
        return header.strip()
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def problem_response(request: Request, status: int, code: str, detail: str,
                     headers: dict[str, str] | None = None) -> JSONResponse:
    body = problem_body(status, code, detail, request.url.path, get_request_id())
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


class McpGateway:
    """Endpoint ASGI `/mcp`: kontrole bramy, potem `handler` (transport Streamable HTTP z SDK)."""

    def __init__(self, handler: ASGIApp, *, max_body_bytes: int) -> None:
        self.handler = handler
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover - trasa HTTP; websocket nie trafia do Route z metodami HTTP
            return
        request = Request(scope, receive)
        state = request.app.state

        wait = state.limiter.hit("ip", client_ip(request), state.settings.daas_rate_limit_ip_per_min)
        if wait > 0:
            await self._too_many(request, wait)(scope, receive, send)
            return

        principal_or_response = await self._authenticate(request)
        if not isinstance(principal_or_response, Principal):
            await principal_or_response(scope, receive, send)
            return

        wait = state.limiter.hit("mcp", principal_or_response.actor_tag or f"ip:{client_ip(request)}",
                                 state.settings.daas_rate_limit_mcp_per_min)
        if wait > 0:
            await self._too_many(request, wait)(scope, receive, send)
            return

        if request.method != "POST":
            response = problem_response(request, 405, "method_not_allowed",
                                        "this MCP server is stateless: use POST with a JSON-RPC message",
                                        headers={"Allow": "POST"})
            await response(scope, receive, send)
            return

        replay = await self._read_body(scope, receive, send, request)
        if replay is None:
            return
        scope.setdefault("state", {})["principal"] = principal_or_response
        await self.handler(scope, replay, send)

    async def _authenticate(self, request: Request) -> Principal | JSONResponse:
        presented = presented_key(request)
        error: str | None = None
        try:
            if presented is None:
                raise unauthorized(request, "missing")
            if presented.startswith(ACCESS_PREFIX):
                # token OAuth konektora: bez organizacji w sciezce, organizacja i zakresy pochodza ze zgody
                oauth = getattr(request.app.state, "oauth", None)
                principal = await oauth.principal_for(presented) if oauth is not None else None
                if principal is None:
                    error = "invalid_token" if oauth is not None else None  # bez OAuth: jak zly klucz
                    raise unauthorized(request, "oauth_token_invalid")
                return principal
            async with request.app.state.db_sessionmaker() as db:
                return await authenticate_api_key(request, db, presented)
        except ProblemError as exc:
            if exc.status_code != 401:  # pragma: no cover - weryfikacja klucza zglasza tylko 401
                raise
            return problem_response(request, 401, "unauthorized", "authentication required",
                                    headers={"WWW-Authenticate": www_authenticate(request, error=error)})
        except HTTPException as exc:  # throttle_auth_failure: 429 po wyczerpaniu budzetu nieudanych prob
            return problem_response(request, exc.status_code, code_for(exc.status_code),
                                    str(exc.detail), headers=exc.headers)

    def _too_many(self, request: Request, wait: float) -> JSONResponse:
        exc = too_many(wait)
        return problem_response(request, 429, "rate_limited", "rate limit exceeded", headers=exc.headers)

    async def _read_body(self, scope: Scope, receive: Receive, send: Send, request: Request) -> Receive | None:
        """Buforuje cialo z limitem (naglowek i faktyczne bajty); zwraca `receive` odtwarzajacy cialo albo None (413)."""
        too_large = problem_response(request, 413, "payload_too_large",
                                     f"request body exceeds {self.max_body_bytes} bytes")
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_body_bytes:
            await too_large(scope, receive, send)
            return None
        body = bytearray()
        trailing: Message | None = None
        while True:
            message = await receive()
            if message["type"] != "http.request":
                trailing = message
                break
            body.extend(message.get("body", b""))
            if len(body) > self.max_body_bytes:
                log.info("mcp request body over the limit")
                await too_large(scope, receive, send)
                return None
            if not message.get("more_body", False):
                break
        queued: deque[Message] = deque([{"type": "http.request", "body": bytes(body), "more_body": False}])
        if trailing is not None:
            queued.append(trailing)

        async def replay() -> Message:
            return queued.popleft() if queued else await receive()

        return replay
