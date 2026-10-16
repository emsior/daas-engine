"""Limity per podmiot dla API v1 (klucz API albo uzytkownik sesji), na tym samym limiterze co trasy legacy."""
from __future__ import annotations

from fastapi import Request

from app.core.ratelimit import client_ip, too_many


def principal_key(request: Request) -> str:
    """Stabilny klucz limitu: `key:<id>` / `user:<id>`; bez uwierzytelnienia adres IP."""
    principal = getattr(request.state, "principal", None)
    tag = getattr(principal, "actor_tag", None)
    return tag or f"ip:{client_ip(request)}"


def enforce(request: Request, rule: str, per_minute: int) -> None:
    """Zuzywa token reguly `rule` dla biezacego podmiotu; po wyczerpaniu 429 + Retry-After."""
    wait = request.app.state.limiter.hit(rule, principal_key(request), per_minute)
    if wait > 0:
        raise too_many(wait)
