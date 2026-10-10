"""Kontrole startowe aplikacji (fail-closed). Wywolywane z `create_app()`."""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from app.core.auth import ApiKeyAuth, is_loopback_host
from app.core.config import MIN_SECRET_KEY_LENGTH, Settings

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

    Tryb `cloud` + `APP_ENV=prod` + `DAAS_EMAIL_BACKEND=console` -> RuntimeError, chyba ze `DAAS_ALLOW_CONSOLE_MAIL=true`
    (linki logowania nie moga cicho trafiac na dysk/do logu produkcji).

    Tryb `cloud`: klucze leza w control plane, wiec DAAS_API_KEY nie jest wymagany ani uzywany; za to
    DAAS_SECRET_KEY (min. 32 znaki) jest wymagany na kazdym adresie — bez niego nie da sie podpisac sesji.
    Tryb `selfhost`: DAAS_SECRET_KEY jest opcjonalny (bez niego logowanie magic linkiem jest wylaczone),
    ale jesli ustawiony, musi miec min. 32 znaki.
    """
    check_single_worker()
    configured_secret = (settings.daas_secret_key or "").strip()
    if settings.cloud and not settings.has_secret_key:
        raise RuntimeError(
            f"DAAS_SECRET_KEY is required in cloud mode (at least {MIN_SECRET_KEY_LENGTH} characters). "
            "Generate one with: openssl rand -hex 32"
        )
    if configured_secret and not settings.has_secret_key:
        raise RuntimeError(f"DAAS_SECRET_KEY is too short: at least {MIN_SECRET_KEY_LENGTH} characters are required.")
    if (settings.cloud and settings.app_env.strip().lower() == "prod"
            and settings.daas_email_backend == "console" and not settings.daas_allow_console_mail):
        raise RuntimeError(
            "DAAS_EMAIL_BACKEND=console is not allowed in cloud mode with APP_ENV=prod: sign-in links would "
            "only land in runtime/outbox/. Set DAAS_EMAIL_BACKEND=smtp (with DAAS_SMTP_HOST), or for a trial "
            "run set DAAS_ALLOW_CONSOLE_MAIL=true."
        )
    if settings.cloud:
        return ApiKeyAuth(None)
    auth = ApiKeyAuth.from_settings(settings)
    if not auth.configured:
        if not is_loopback_host(settings.app_host):
            raise RuntimeError(
                "DAAS_API_KEY is required when APP_HOST is not a loopback address "
                f"(APP_HOST={settings.app_host!r}). Set DAAS_API_KEY (min 16 characters)."
            )
        log.warning("DAAS_API_KEY is not set: every protected route will answer 401 until it is configured.")
    return auth
