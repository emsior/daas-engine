"""Uwierzytelnianie API kluczem w naglowku `X-API-Key` (fail-closed).

Zasady:
    * Wszystkie trasy poza `/health` i `/ready` wymagaja klucza (zaleznosc `require_api_key` na routerze).
    * Brak skonfigurowanego klucza NIE otwiera API: kazde zapytanie dostaje 401, a start na adresie innym
      niz loopback jest odmawiany (patrz `app.core.startup`).
    * W pamieci trzymany jest tylko skrot SHA-256 klucza. Porownanie to `hmac.compare_digest` na dwoch
      skrotach tej samej dlugosci, wiec czas nie zalezy od dlugosci ani zawartosci podanego klucza.
    * Wartosc klucza nie trafia do logow, audytu ani odpowiedzi (audyt zapisuje tylko kod powodu).

Model jest celowo prosty (jeden klucz na wdrozenie). Klucze per organizacja wejda z control plane
bez zmiany kontraktu klienta: naglowek i kody odpowiedzi zostaja takie same.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.core.config import Settings
from app.core.ratelimit import throttle_auth_failure
from app.core.upload_guard import ReasonCode

API_KEY_HEADER = "X-API-Key"
MIN_KEY_LENGTH = 16

api_key_scheme = APIKeyHeader(
    name=API_KEY_HEADER,
    auto_error=False,  # odmowe budujemy sami: audyt + jednolity ksztalt bledu
    scheme_name="ApiKeyAuth",
    description="Klucz API wdrozenia (DAAS_API_KEY).",
)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def is_loopback_host(host: str) -> bool:
    """True dla `localhost` i adresow petli zwrotnej (127.0.0.0/8, ::1)."""
    value = host.strip().strip("[]").lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class ApiKeyAuth:
    """Stan uwierzytelniania aplikacji. `key_hash is None` = klucz nieskonfigurowany (odmowa wszystkiego)."""

    key_hash: bytes | None = field(default=None, repr=False)

    @property
    def configured(self) -> bool:
        return self.key_hash is not None

    @classmethod
    def from_settings(cls, settings: Settings) -> ApiKeyAuth:
        """Buduje stan z konfiguracji. Zbyt krotki klucz to blad konfiguracji, nie ciche przyjecie."""
        if not settings.has_api_key:
            return cls(None)
        key = (settings.daas_api_key or "").strip()
        if len(key) < MIN_KEY_LENGTH:
            raise RuntimeError(
                f"DAAS_API_KEY is too short: at least {MIN_KEY_LENGTH} characters are required."
            )
        return cls(_digest(key))

    def check(self, presented: str | None) -> str:
        """Zwraca `ReasonCode.OK` albo kod odmowy. Nie rzuca wyjatkow."""
        if self.key_hash is None:
            return ReasonCode.AUTH_NOT_CONFIGURED
        if not presented or not presented.strip():
            return ReasonCode.AUTH_MISSING
        if hmac.compare_digest(_digest(presented.strip()), self.key_hash):
            return ReasonCode.OK
        return ReasonCode.AUTH_INVALID


def require_api_key(request: Request, presented: str | None = Security(api_key_scheme)) -> None:
    """Zaleznosc FastAPI: przepuszcza zapytanie z poprawnym kluczem, w przeciwnym razie 401.

    Odpowiedz jest identyczna dla braku, blednego i nieskonfigurowanego klucza — wywolujacy nie
    dowiaduje sie, co jest nie tak. Powod trafia do audytu (`auth_denied`).
    """
    from app.core import audit

    auth: ApiKeyAuth = request.app.state.auth
    reason = auth.check(presented)
    if reason == ReasonCode.OK:
        return
    throttle_auth_failure(request)  # po wyczerpaniu budzetu bledow: 429 i brak wpisu audytu
    audit.record(request, action="auth_denied", allowed=False, reason=reason)
    raise HTTPException(
        status_code=401,
        detail="authentication required",
        headers={"WWW-Authenticate": "ApiKey"},
    )
