"""Limity zadan: token bucket w pamieci procesu (bez zaleznosci).

Reguly (wszystkie konfigurowalne, 0 = wylaczona; patrz `Settings.daas_rate_limit_*`):
    * `ip`        — wszystkie trasy chronione, per adres klienta, sprawdzane PRZED uwierzytelnieniem,
    * `run`       — POST /run, per klucz API,
    * `upload`    — POST /upload, per klucz API,
    * `auth_fail` — nieudane uwierzytelnienia, per adres klienta (patrz `throttle_auth_failure`).

Pojemnosc kubelka = limit na minute (serie dozwolone), uzupelnianie = limit/60 na sekunde, wiec kazdy
kubelek odzyskuje pelna pojemnosc w 60 s — dzieki temu bezczynne kubelki mozna bezpiecznie porzucac.

OGRANICZENIE: liczniki sa w pamieci jednego procesu, dlatego aplikacja wymaga jednego workera
(`app.core.startup.check_single_worker`). Za reverse proxy adres klienta to adres proxy, chyba ze serwer
ASGI ma wlaczone `--proxy-headers` z `--forwarded-allow-ips` (uvicorn: domyslnie tylko 127.0.0.1).
Po restarcie procesu liczniki zaczynaja od zera.
"""
from __future__ import annotations

import hashlib
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request

#: Po tylu sekundach bezczynnosci kazdy kubelek jest znowu pelny, wiec rownowazny swiezemu.
FULL_REFILL_SEC = 60.0


@dataclass
class _Bucket:
    tokens: float
    stamp: float


class RateLimiter:
    """Watkowo-bezpieczny zbior kubelkow tokenow indeksowanych (regula, klucz)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, max_buckets: int = 10_000) -> None:
        self._clock = clock
        self._max = max_buckets
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def hit(self, rule: str, key: str, per_minute: int) -> float:
        """Zuzywa jeden token. Zwraca 0.0 gdy dozwolone, inaczej liczbe sekund do kolejnej proby."""
        if per_minute <= 0:
            return 0.0
        now = self._clock()
        rate = per_minute / 60.0
        with self._lock:
            bucket = self._buckets.get((rule, key))
            if bucket is None:
                self._prune(now)
                bucket = self._buckets[(rule, key)] = _Bucket(tokens=float(per_minute), stamp=now)
            else:
                bucket.tokens = min(float(per_minute), bucket.tokens + (now - bucket.stamp) * rate)
                bucket.stamp = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return 0.0
            return (1.0 - bucket.tokens) / rate

    def size(self) -> int:
        with self._lock:
            return len(self._buckets)

    def _prune(self, now: float) -> None:
        """Porzuca bezczynne kubelki; przy nadal pelnej tabeli usuwa ~10% najstarszych (amortyzowane O(1))."""
        if len(self._buckets) < self._max:
            return
        for k in [k for k, b in self._buckets.items() if now - b.stamp >= FULL_REFILL_SEC]:
            del self._buckets[k]
        if len(self._buckets) >= self._max:
            oldest = sorted(self._buckets, key=lambda k: self._buckets[k].stamp)
            for k in oldest[: max(1, self._max // 10)]:
                del self._buckets[k]


# --------------------------------------------------------------------------- zaleznosci FastAPI
def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _key_id(request: Request) -> str:
    """Skrot podanego klucza (po uwierzytelnieniu to zawsze poprawny klucz); sama wartosc nigdzie nie trafia."""
    presented = request.headers.get("x-api-key", "")
    return hashlib.sha256(presented.encode("utf-8")).hexdigest()[:16]


def too_many(wait_sec: float) -> HTTPException:
    return HTTPException(status_code=429, detail="rate limit exceeded",
                         headers={"Retry-After": str(max(1, math.ceil(wait_sec)))})


def limit_ip(request: Request) -> None:
    """Regula `ip`: wszystkie trasy chronione, przed uwierzytelnieniem (rowniez zalew anonimowych zapytan)."""
    wait = request.app.state.limiter.hit("ip", client_ip(request), request.app.state.settings.daas_rate_limit_ip_per_min)
    if wait > 0:
        raise too_many(wait)


def limit_run(request: Request) -> None:
    wait = request.app.state.limiter.hit("run", _key_id(request), request.app.state.settings.daas_rate_limit_run_per_min)
    if wait > 0:
        raise too_many(wait)


def limit_upload(request: Request) -> None:
    wait = request.app.state.limiter.hit(
        "upload", _key_id(request), request.app.state.settings.daas_rate_limit_upload_per_min)
    if wait > 0:
        raise too_many(wait)


def throttle_auth_failure(request: Request) -> None:
    """Wywolywane przy KAZDYM nieudanym uwierzytelnieniu. Po wyczerpaniu budzetu bledow z danego adresu
    nastepne nieudane proby dostaja 429 (i nie trafiaja do audytu — dziennik nie rosnie bez ograniczen).

    Poprawny klucz nigdy nie jest blokowany: adres klienta bywa wspolny (reverse proxy), a klucz jest
    wystarczajaco dlugi, by zgadywanie bylo nierealne; chodzi o ograniczenie halasu, nie o blokade wlasciciela.
    """
    wait = request.app.state.limiter.hit(
        "auth_fail", client_ip(request), request.app.state.settings.daas_rate_limit_auth_fail_per_min)
    if wait > 0:
        raise too_many(wait)
