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
import threading
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.problems import PROBLEM_MEDIA_TYPE, is_problem_path, is_v1_path, problem_body

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
                path = scope.get("path", "")
                if is_problem_path(path):
                    payload = problem_body(500, "internal_error", "internal server error", path, rid)
                    media = PROBLEM_MEDIA_TYPE
                else:
                    payload = {"detail": "internal server error", "code": "internal_error", "request_id": rid}
                    media = "application/json"
                body = json.dumps(payload).encode()
                await send_with_id({"type": "http.response.start", "status": 500, "headers": [
                    (b"content-type", media.encode()), (b"content-length", str(len(body)).encode())]})
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


# --------------------------------------------------------------------------- trasy legacy
class DeprecationMiddleware:
    """Dokleja `Deprecation: true` i `Link: </api/v1>; rel="successor-version"` do tras legacy (tryb selfhost).

    Dziala na poziomie ASGI, bo odpowiedzi typu `HTMLResponse`/`FileResponse` nie dziedzicza naglowkow
    ustawionych przez zaleznosci FastAPI.
    """

    def __init__(self, app: ASGIApp, paths: Iterable[str]) -> None:
        self.app = app
        self.prefixes = tuple(paths)

    def _is_legacy(self, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in self.prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_legacy(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        async def send_marked(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Deprecation"] = "true"
                headers["Link"] = '</api/v1>; rel="successor-version"'
            await send(message)

        await self.app(scope, receive, send_marked)


# --------------------------------------------------------------------------- metryki
#: Granice histogramu czasu odpowiedzi (sekundy), jak domyslne w bibliotekach klienckich Prometheusa.
DURATION_BUCKETS: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


class Metrics:
    """Liczniki HTTP w pamieci procesu. Etykiety: szablon trasy i status — nigdy sciezka z identyfikatorami,
    parametry ani dane klienta (kardynalnosc ograniczona liczba tras)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.http: dict[tuple[str, int], int] = {}
        #: per szablon trasy: `[liczniki kubelkow (nie skumulowane) + (+Inf), suma sekund]`
        self.durations: dict[str, list[float]] = {}

    def observe(self, route: str, status: int, duration_sec: float | None = None) -> None:
        with self._lock:
            self.http[(route, status)] = self.http.get((route, status), 0) + 1
            if duration_sec is None:
                return
            hist = self.durations.setdefault(route, [0.0] * (len(DURATION_BUCKETS) + 2))
            index = next((i for i, bound in enumerate(DURATION_BUCKETS) if duration_sec <= bound),
                         len(DURATION_BUCKETS))
            hist[index] += 1
            hist[-1] += duration_sec

    def snapshot(self) -> dict[tuple[str, int], int]:
        with self._lock:
            return dict(self.http)

    def duration_snapshot(self) -> dict[str, list[float]]:
        with self._lock:
            return {route: list(hist) for route, hist in self.durations.items()}


def _label(value: object) -> str:
    """Wartosc etykiety wg formatu ekspozycji 0.0.4: ucieczki dla `\\`, `"` i nowej linii."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(value: float) -> str:
    return repr(int(value)) if float(value).is_integer() else repr(round(value, 6))


def render_prometheus(metrics: Metrics, *, job_counts: dict[str, int], active_tenants: int,
                      version: str, mode: str) -> str:
    """Tekst w formacie ekspozycji Prometheus 0.0.4. Etykiety wylacznie: szablon trasy, status HTTP, status zadania,
    wersja i tryb — zadnych identyfikatorow organizacji, adresow e-mail ani nazw plikow."""
    out: list[str] = [
        "# HELP daas_build_info Wersja aplikacji i tryb pracy.",
        "# TYPE daas_build_info gauge",
        f'daas_build_info{{version="{_label(version)}",mode="{_label(mode)}"}} 1',
        "# HELP daas_http_requests_total Zapytania HTTP wg szablonu trasy i statusu.",
        "# TYPE daas_http_requests_total counter",
    ]
    for (route, status), count in sorted(metrics.snapshot().items()):
        out.append(f'daas_http_requests_total{{route="{_label(route)}",status="{status}"}} {count}')
    out += [
        "# HELP daas_http_request_duration_seconds Czas obslugi zapytania HTTP wg szablonu trasy.",
        "# TYPE daas_http_request_duration_seconds histogram",
    ]
    for route, hist in sorted(metrics.duration_snapshot().items()):
        cumulative = 0.0
        for bound, bucket in zip((*DURATION_BUCKETS, float("inf")), hist[:-1], strict=True):
            cumulative += bucket
            le = "+Inf" if bound == float("inf") else repr(bound)
            out.append(f'daas_http_request_duration_seconds_bucket{{route="{_label(route)}",le="{le}"}} '
                       f"{_num(cumulative)}")
        out.append(f'daas_http_request_duration_seconds_sum{{route="{_label(route)}"}} {_num(hist[-1])}')
        out.append(f'daas_http_request_duration_seconds_count{{route="{_label(route)}"}} {_num(cumulative)}')
    out += [
        "# HELP daas_jobs Liczba zadan wg statusu (stan biezacy control plane).",
        "# TYPE daas_jobs gauge",
    ]
    out += [f'daas_jobs{{status="{_label(status)}"}} {count}' for status, count in sorted(job_counts.items())]
    out += [
        "# HELP daas_active_tenants Organizacje z otwartym runnerem danych (rejestr LRU).",
        "# TYPE daas_active_tenants gauge",
        f"daas_active_tenants {active_tenants}",
    ]
    return "\n".join(out) + "\n"


def route_template(scope: Scope) -> str:
    """Szablon trasy do etykiety: `/api/v1/orgs/{org_id}/jobs`, nigdy realna sciezka. W nowszym FastAPI
    `route.path` bywa wzgledne wobec routera (bez `/api/v1`), wiec prefiks dopisujemy na podstawie sciezki zadania."""
    path = getattr(scope.get("route"), "path", None)
    if not path:
        return "unmatched"
    if is_v1_path(scope.get("path", "")) and not is_v1_path(path):
        return "/api/v1" + path
    return path


class MetricsMiddleware:
    """Zlicza zapytania HTTP wg szablonu trasy (`/api/v1/orgs/{org_id}/jobs`), statusu i czasu obslugi."""

    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = 500
        started = time.perf_counter()

        async def send_counting(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_counting)
        finally:
            self.metrics.observe(route_template(scope), status, time.perf_counter() - started)
