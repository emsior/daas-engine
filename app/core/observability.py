"""Request-id, logi JSON (stdlib), naglowki bezpieczenstwa API i dziennik zadan.

Trzy elementy, wszystkie jako czyste middleware ASGI (bez BaseHTTPMiddleware):
    * `RequestIdMiddleware`      — `X-Request-ID` (przyjety, gdy poprawny; inaczej nowy), contextvar dla logow,
                                   linia dziennika dostepu i generyczna odpowiedz 500 z request_id,
    * `SecurityHeadersMiddleware` — nosniff, Referrer-Policy, CSP dla API, HSTS wylacznie przez HTTPS,
    * `JsonFormatter` / `configure_logging` — jedna linia JSON na rekord, z request_id.
Do logow nie trafiaja naglowki ani query string.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("daas_request_id", default=None)
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

REQUEST_ID_HEADER = "X-Request-ID"
API_CSP = "default-src 'none'; frame-ancestors 'none'"
HSTS = "max-age=31536000; includeSubDomains"

access_log = logging.getLogger("daas.access")
log = logging.getLogger("daas")


def get_request_id() -> str | None:
    """request_id biezacego zapytania (None poza zapytaniem HTTP)."""
    return _request_id.get()


# --------------------------------------------------------------------------- logi JSON
class JsonFormatter(logging.Formatter):
    """Jedna linia JSON na rekord: ts, level, logger, msg, request_id, pola z `extra={"fields": {...}}`."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = get_request_id()
        if rid:
            out["request_id"] = rid
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            out.update(fields)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


class _JsonStderrHandler(logging.Handler):
    """Zapis na biezacy `sys.stderr` (rozwiazywany przy kazdym rekordzie — dziala z przechwytywaniem w testach)."""

    _daas_json = True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:  # noqa: BLE001  logging.Handler.handleError wypisuje blad bez rzucania
            self.handleError(record)


def configure_logging(level: str = "INFO") -> None:
    """Instaluje handler JSON na root loggerze (idempotentnie: powtorne wywolanie nie dubluje linii)."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_daas_json", False)]:
        root.removeHandler(handler)
    handler = _JsonStderrHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


# --------------------------------------------------------------------------- request-id
def _incoming_request_id(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            if _VALID_REQUEST_ID.match(candidate):
                return candidate
            break
    return uuid.uuid4().hex


class RequestIdMiddleware:
    """Nadaje request_id, loguje zapytanie i zamienia nieobsluzony wyjatek na generyczne 500."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = _incoming_request_id(scope)
        token = _request_id.set(rid)
        scope.setdefault("state", {})["request_id"] = rid
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = rid
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_id)
            except Exception:
                # Szczegoly tylko do logu (z request_id); klient dostaje kod i identyfikator.
                log.exception("unhandled exception method=%s path=%s", scope.get("method"), scope.get("path"))
                if response_started:
                    raise
                body = json.dumps({"detail": "internal server error", "code": "internal_error",
                                   "request_id": rid}).encode()
                await send_with_id({"type": "http.response.start", "status": 500, "headers": [
                    (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send_with_id({"type": "http.response.body", "body": body})
        finally:
            access_log.info("request", extra={"fields": {
                "method": scope.get("method"), "path": scope.get("path"), "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "client_ip": (scope.get("client") or (None,))[0],
            }})
            _request_id.reset(token)


# --------------------------------------------------------------------------- naglowki bezpieczenstwa
class SecurityHeadersMiddleware:
    """Dokleja naglowki bezpieczenstwa, nie nadpisujac tych, ktore ustawila trasa (np. CSP raportu)."""

    def __init__(self, app: ASGIApp, csp_exempt_paths: Iterable[str] = ()) -> None:
        self.app = app
        self.csp_exempt = frozenset(csp_exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        https = scope.get("scheme") == "https"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                if path not in self.csp_exempt:
                    headers.setdefault("Content-Security-Policy", API_CSP)
                if https:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)
