"""Webhooki wychodzace: dostarczanie `run.finished` (i `ping`) z podpisem HMAC, ponowieniami i ochrona przed SSRF.

Kontrakt dostarczenia:
    * `POST <url>` z `Content-Type: application/json`; naglowki `X-DaaS-Event`, `X-DaaS-Delivery` (ULID, ten sam przy
      ponowieniach), `X-DaaS-Timestamp` (sekundy unix, osobno przy kazdej probie) i
      `X-DaaS-Signature: sha256=<hex HMAC-SHA256(sekret, cialo)>`; podpis obejmuje wylacznie cialo,
    * cialo = pola `RunFinished` (bez tresci raportu i danych klienta); `ping` niesie tylko `event` i `org_id`,
    * sukces = odpowiedz 2xx; przekierowania NIE sa sledzone (3xx = porazka), timeout 5 s,
    * najwyzej `MAX_ATTEMPTS` prob; przerwy wg `DAAS_WEBHOOK_BACKOFF_SEC` (przerwa nr i przed proba i+1),
    * dostawa dziala w tle (task asyncio), wiec nie blokuje executora; stan nie jest trwaly — po restarcie
      oczekujace ponowienia przepadaja (jest wpis `webhook.failed` tylko dla zakonczonych dostaw),
    * poza `APP_ENV=dev` adres musi byc `https` i wskazywac wylacznie adresy publiczne (SSRF).
Sekret podpisu jest pochodna `DAAS_SECRET_KEY` i id webhooka (zob. `security.webhook_signing_secret`); w bazie
jest tylko jego skrot, uzywany tu do wykrycia rotacji `DAAS_SECRET_KEY` (podpis innym sekretem nie jest wysylany).
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.control.audit import record_event
from app.control.executor import RunFinished
from app.control.ids import new_ulid
from app.control.models import Webhook
from app.control.security import safe_equal, sha256_hex, sign_payload, webhook_signing_secret
from app.core.config import Settings

log = logging.getLogger(__name__)

EVENT_RUN_FINISHED = "run.finished"
EVENT_PING = "ping"
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_SEC = 5.0
MAX_URL_LEN = 2000
USER_AGENT = "DaaS-Webhook/2.0"
_BLOCKED_NAMES = ("localhost", "localhost.localdomain")
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")

Resolver = Callable[[str, int], Awaitable[list[str]]]
Sleeper = Callable[[float], Awaitable[None]]


class UnsafeDestination(ValueError):
    """Adres webhooka odrzucony; `reason` to stabilny kod maszynowy (bez adresu)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def is_dev(settings: Settings) -> bool:
    return settings.app_env.strip().lower() == "dev"


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def validate_url(url: str, *, dev: bool) -> tuple[str, int]:
    """Skladnia i oczywiste adresy wewnetrzne (bez DNS). Zwraca `(host, port)`; rzuca `UnsafeDestination`."""
    if len(url) > MAX_URL_LEN or any(c in url for c in " \t\r\n\x00"):
        raise UnsafeDestination("url_malformed")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise UnsafeDestination("url_malformed") from None
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("https", "http") or not host:
        raise UnsafeDestination("url_malformed")
    if parts.scheme != "https" and not dev:
        raise UnsafeDestination("https_required")
    if parts.username is not None or parts.password is not None:
        raise UnsafeDestination("credentials_in_url")
    if parts.fragment:
        raise UnsafeDestination("url_malformed")
    if not dev:
        if host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES) or "." not in host and ":" not in host:
            raise UnsafeDestination("private_address")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass  # nazwa DNS: adresy sprawdza `check_resolved` przy kazdej dostawie
        else:
            if not _is_public_ip(host):
                raise UnsafeDestination("private_address")
    return host, port or (443 if parts.scheme == "https" else 80)


async def system_resolver(host: str, port: int) -> list[str]:
    """Wszystkie adresy hosta (A/AAAA). Wstrzykiwalne w testach — produkcyjnie `getaddrinfo` w watku."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


async def check_resolved(host: str, port: int, resolver: Resolver) -> None:
    """Kazdy adres, na jaki rozwiazuje sie host, ma byc publiczny (inaczej `private_address`)."""
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            addresses = await resolver(host, port)
        except OSError:
            raise UnsafeDestination("dns_failed") from None
    if not addresses or not all(_is_public_ip(a) for a in addresses):
        raise UnsafeDestination("private_address")


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    status_code: int | None
    attempts: int
    delivery_id: str
    error_type: str | None = None


def build_body(event: str, fields: dict[str, str]) -> bytes:
    """Cialo zadania: deterministyczny JSON (posortowane klucze, bez spacji) — to jest podpisywane."""
    return json.dumps({"event": event, **fields}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def run_finished_fields(event: RunFinished) -> dict[str, str]:
    return {"org_id": event.org_id, "job_id": event.job_id, "run_id": event.run_id, "pipeline": event.pipeline,
            "status": event.status, "run_status": event.run_status, "finished_at": event.finished_at_iso}


class WebhookDispatcher:
    """Dostarcza zdarzenia do webhookow organizacji. `on_run_finished` pasuje do `executor.Listener`."""

    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], *,
                 transport: httpx.AsyncBaseTransport | None = None, resolver: Resolver = system_resolver,
                 sleep: Sleeper = asyncio.sleep) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.transport = transport  # testy podstawiaja httpx.MockTransport
        self.resolver = resolver
        self.sleep = sleep
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ cykl zycia
    async def close(self) -> None:
        """Przerywa dostawy w toku (ponowienia w pamieci nie przezywaja zamkniecia procesu)."""
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def drain(self, timeout: float = 30.0) -> None:
        """Czeka na zakonczenie biezacych dostaw (testy, zamkniecie kontrolowane)."""
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=timeout)

    # ------------------------------------------------------------------ run.finished
    async def on_run_finished(self, event: RunFinished) -> None:
        """Listener executora: wybiera webhooki organizacji i startuje dostawy w tle (nie blokuje runu)."""
        async with self.sessionmaker() as db:
            hooks = (await db.execute(select(Webhook).where(Webhook.org_id == event.org_id, Webhook.active.is_(True))
                                      .order_by(Webhook.id))).scalars().all()
        body = build_body(EVENT_RUN_FINISHED, run_finished_fields(event))
        for hook in hooks:
            if EVENT_RUN_FINISHED not in (hook.events or []):
                continue
            task = asyncio.get_running_loop().create_task(
                self._deliver_and_audit(hook, EVENT_RUN_FINISHED, body, MAX_ATTEMPTS),
                name=f"webhook-{hook.id}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _deliver_and_audit(self, hook: Webhook, event_name: str, body: bytes, max_attempts: int) -> None:
        try:
            result = await self.deliver(hook, event_name, body, max_attempts=max_attempts)
            await self.audit_result(None, hook, event_name, result)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("webhook delivery crashed webhook_id=%s", hook.id)

    async def audit_result(self, request, hook: Webhook, event_name: str, result: DeliveryResult, *,
                           actor_type: str = "system", actor_id: str | None = None) -> None:
        async with self.sessionmaker() as db:
            await record_event(
                db, request, org_id=hook.org_id, actor_type=actor_type, actor_id=actor_id,
                action="webhook.delivered" if result.delivered else "webhook.failed", target=f"webhook:{hook.id}",
                settings=self.settings,
                meta={"webhook_id": hook.id, "delivery_id": result.delivery_id, "event": event_name,
                      "attempt": result.attempts, "status_code": result.status_code,
                      "error_type": result.error_type})
            await db.commit()

    # ------------------------------------------------------------------ dostarczenie
    def signing_secret(self, hook: Webhook) -> str | None:
        """Sekret podpisu albo None, gdy brak `DAAS_SECRET_KEY` lub klucz zrotowano (skrot sie nie zgadza)."""
        master = self.settings.daas_secret_key
        if not master:
            return None
        secret = webhook_signing_secret(hook.id, master)
        return secret if safe_equal(sha256_hex(secret), hook.secret_hash) else None

    async def deliver(self, hook: Webhook, event_name: str, body: bytes, *,
                      max_attempts: int = MAX_ATTEMPTS) -> DeliveryResult:
        """Wysyla `body` do webhooka (z ponowieniami). Nie rzuca przy bledach sieci/odbiorcy — zwraca wynik."""
        delivery_id = new_ulid()
        secret = self.signing_secret(hook)
        if secret is None:
            log.warning("webhook skipped: signing secret unavailable webhook_id=%s", hook.id)
            return DeliveryResult(False, None, 0, delivery_id, "secret_unavailable")
        signature = sign_payload(body, secret)
        backoff = self.settings.webhook_backoff
        status: int | None = None
        error_type: str | None = None
        attempts = 0
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            status, error_type = await self._attempt(hook, event_name, body, delivery_id, signature)
            if status is not None and 200 <= status < 300:
                return DeliveryResult(True, status, attempts, delivery_id)
            if error_type in {"private_address", "dns_failed", "https_required", "url_malformed",
                              "credentials_in_url"}:
                break  # adres sie nie poprawi miedzy probami
            if attempt < max_attempts:
                index = attempt - 1
                await self.sleep(backoff[index] if index < len(backoff) else (backoff[-1] if backoff else 0.0))
        log.info("webhook delivery failed webhook_id=%s attempts=%d status=%s error=%s",
                 hook.id, attempts, status, error_type)
        return DeliveryResult(False, status, attempts, delivery_id, error_type or "http_status")

    async def _attempt(self, hook: Webhook, event_name: str, body: bytes, delivery_id: str,
                       signature: str) -> tuple[int | None, str | None]:
        """Jedna proba. Zwraca `(status HTTP | None, typ bledu | None)`; adres nie trafia do logow."""
        dev = is_dev(self.settings)
        try:
            host, port = validate_url(hook.url, dev=dev)
            if not dev:
                await check_resolved(host, port, self.resolver)
        except UnsafeDestination as exc:
            return None, exc.reason
        headers = {
            "Content-Type": "application/json", "User-Agent": USER_AGENT, "X-DaaS-Event": event_name,
            "X-DaaS-Delivery": delivery_id, "X-DaaS-Timestamp": str(int(time.time())),
            "X-DaaS-Signature": signature,
        }
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC, follow_redirects=False,
                                         transport=self.transport) as client:
                # strumien: tresc odpowiedzi odbiorcy nie jest czytana ani zapisywana
                async with client.stream("POST", hook.url, content=body, headers=headers) as response:
                    return response.status_code, None
        except httpx.HTTPError as exc:
            return None, type(exc).__name__
