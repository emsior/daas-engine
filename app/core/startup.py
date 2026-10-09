"""Kontrole startowe aplikacji (fail-closed). Wywolywane z `create_app()`."""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from app.core.auth import ApiKeyAuth, is_loopback_host
from app.core.config import Settings

log = logging.getLogger(__name__)

#: Zmienne, przez ktore serwer ASGI dostaje liczbe procesow roboczych.
WORKER_ENV_VARS = ("WEB_CONCURRENCY", "UVICORN_WORKERS")


def check_single_worker(environ: Mapping[str, str] | None = None) -> None:
    """Odmawia startu, gdy srodowisko zleca wiecej niz jeden proces roboczy.

    Powod: `DuckDBClient` serializuje zapisy zamkiem w obrebie jednego procesu (`threading.RLock`),
    a limiter zadan (`app.core.ratelimit`) trzyma liczniki w pamieci procesu. Kilka procesow
    rywalizowaloby o plik DuckDB i mialoby osobne limity. Wartosci nieliczbowe sa ignorowane.
    """
    env = os.environ if environ is None else environ
    for name in WORKER_ENV_VARS:
        raw = (env.get(name) or "").strip()
        if raw.isdigit() and int(raw) > 1:
            raise RuntimeError(
                f"{name}={raw}: DaaS Engine must run with exactly one worker process "
                "(DuckDB single writer, in-process lock and rate limiter). Remove the setting or use 1."
            )


def validate_startup(settings: Settings) -> ApiKeyAuth:
    """Sprawdza konfiguracje bezpieczenstwa i zwraca stan uwierzytelniania.

    * klucz zbyt krotki -> RuntimeError (z `ApiKeyAuth.from_settings`),
    * brak klucza + adres inny niz loopback -> RuntimeError,
    * brak klucza + loopback -> start dozwolony, ale kazda trasa chroniona odpowiada 401 (ostrzezenie w logu).
    """
    check_single_worker()
    auth = ApiKeyAuth.from_settings(settings)
    if not auth.configured:
        if not is_loopback_host(settings.app_host):
            raise RuntimeError(
                "DAAS_API_KEY is required when APP_HOST is not a loopback address "
                f"(APP_HOST={settings.app_host!r}). Set DAAS_API_KEY (min 16 characters)."
            )
        log.warning("DAAS_API_KEY is not set: every protected route will answer 401 until it is configured.")
    return auth
