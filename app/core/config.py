"""Konfiguracja aplikacji (Pydantic Settings) z auto-detekcją braku sekretów.

Zasada: brak klucza NIGDY nie jest błędem. Pipeline przełącza się na fixtures
i oznacza wynik jako MOCK_DATA.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Katalog projektu: wzgledne sciezki (runtime/, data/fixtures, alembic/) sa liczone od niego.
#: DAAS_HOME nadpisuje go tam, gdzie pakiet jest zainstalowany w site-packages (obraz Docker).
PROJECT_ROOT = Path(os.environ.get("DAAS_HOME") or Path(__file__).resolve().parents[2]).resolve()

#: Minimalna dlugosc DAAS_SECRET_KEY.
MIN_SECRET_KEY_LENGTH = 32

#: Dozwolony identyfikator organizacji w sciezce katalogu (ULID, `default`); bez separatorow i kropek.
ORG_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

#: Jedyna nazwa pliku audytowego w calym projekcie. Nie duplikowac literalem.
AUDIT_LOG_FILENAME = "audit.jsonl"


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == "" or value.strip().lower() in {"none", "null", "changeme", "change_me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- aplikacja ---
    #: "dev" wlacza /docs, /redoc, /openapi.json i auto-reload. Wszystko inne (domyslnie "prod") je wylacza.
    app_env: str = "prod"
    app_name: str = "DaaS Engine"
    #: Wersja awaryjna; zrodlem prawdy w /health jest wersja zainstalowanego pakietu (installed_version()).
    app_version: str = "2.0.0"
    #: Domyslnie loopback. W kontenerze obraz ustawia APP_HOST=0.0.0.0 jawnie (Dockerfile).
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    log_level: str = "INFO"
    daas_policy_enforce: bool = Field(
        default=True,
        description="Policy w runtime (granica katalogu roboczego, audyt, fail-closed). DAAS_POLICY_ENFORCE=false wyłącza.",
    )
    daas_llm_narrative: bool = Field(
        default=False,
        description="Opt-in: streszczenie raportu przez zewnętrzny LLM. Sam ANTHROPIC_API_KEY niczego nie włącza.",
    )
    #: Klucz API (naglowek X-API-Key). Brak = fail-closed: kazda trasa poza /health i /ready odpowiada 401,
    #: a start na adresie innym niz loopback jest odmawiany. Minimum 16 znakow. Nigdy nie trafia do odpowiedzi ani logow.
    daas_api_key: str | None = Field(default=None, repr=False)
    # --- limity zadan i uploadow (0 = wylaczone). Liczniki w pamieci procesu: wymagany jeden worker. ---
    daas_rate_limit_run_per_min: int = Field(default=30, ge=0, description="POST /run, per klucz API")
    daas_rate_limit_upload_per_min: int = Field(default=30, ge=0, description="POST /upload, per klucz API")
    daas_rate_limit_ip_per_min: int = Field(default=300, ge=0, description="wszystkie trasy chronione, per adres IP")
    daas_rate_limit_auth_fail_per_min: int = Field(default=10, ge=0, description="nieudane klucze, per adres IP")
    daas_upload_quota_bytes: int = Field(
        default=1_073_741_824, ge=0, description="laczna kwota bajtow w uploads/ (1 GiB); 0 = bez limitu")
    # --- tryb pracy i control plane (patrz docs/ARCHITECTURE.md) ---
    #: `selfhost` = jeden najemca (`default`), klucz z DAAS_API_KEY, trasy legacy dzialaja.
    #: `cloud` = wielu najemcow, wszystko przez /api/v1, trasy legacy odpowiadaja 404.
    daas_mode: Literal["selfhost", "cloud"] = "selfhost"
    #: Baza control plane (SQLAlchemy async URL). SQLite domyslnie; Postgres: postgresql+asyncpg://...
    daas_control_db_url: str = "sqlite+aiosqlite:///runtime/control.db"
    #: Przy starcie aplikacji wykonaj `alembic upgrade head` (idempotentne). Wylacz, gdy migracje robi operator.
    daas_auto_migrate: bool = True
    #: Sekret podpisu cookie sesji i kluczy podpisu webhookow. Wymagany w trybie cloud (min. 32 znaki).
    daas_secret_key: str | None = Field(default=None, repr=False)
    #: Jedyny origin kokpitu dopuszczony przez CORS (z credentials). Brak = CORS wylaczony.
    daas_cockpit_origin: str | None = None
    #: Atrybut Secure cookie sesji. `false` tylko do lokalnego dev po http.
    daas_cookie_secure: bool = True
    daas_session_ttl_hours: int = Field(default=336, ge=1, description="waznosc sesji (domyslnie 14 dni)")
    daas_magic_link_ttl_minutes: int = Field(default=15, ge=1, le=120)
    daas_email_backend: Literal["console", "smtp"] = "console"
    #: Jawna zgoda na `console` w trybie cloud przy APP_ENV=prod (linki logowania leza wtedy w runtime/outbox/).
    #: Bez niej `validate_startup` odmawia startu. Tylko do prob; produkcja: `DAAS_EMAIL_BACKEND=smtp`.
    daas_allow_console_mail: bool = False
    daas_email_from: str = "DaaS Engine <no-reply@localhost>"
    daas_smtp_host: str | None = None
    daas_smtp_port: int = Field(default=587, ge=1, le=65535)
    daas_smtp_user: str | None = None
    daas_smtp_password: str | None = Field(default=None, repr=False)
    daas_smtp_starttls: bool = True
    #: Globalny limit rownoczesnych runow (wszystkie organizacje); w obrebie organizacji zawsze 1.
    daas_max_concurrent_runs: int = Field(default=2, ge=1, le=64)
    #: Ile organizacji ma jednoczesnie otwarty runner (LRU); starsze sa zamykane.
    daas_max_active_tenants: int = Field(default=16, ge=1, le=1024)
    #: Token dla GET /metrics (Authorization: Bearer ...). Bez tokenu /metrics odpowiada 404.
    daas_metrics_token: str | None = Field(default=None, repr=False)
    #: Domyslna retencja uploadow nowej organizacji, w dniach.
    daas_retention_days: int = Field(default=90, ge=1, le=3650)
    #: Co ile minut tryb cloud uruchamia retencje (kasowanie uploadow, raportow i danych po terminie); 0 = wylaczone.
    daas_retention_interval_min: int = Field(default=60, ge=0, le=10080)
    #: Petla harmonogramu (przypomnienia o eksporcie): tylko tryb cloud; `false` wylacza ja w tym procesie.
    daas_scheduler_enabled: bool = True
    #: Co ile sekund petla harmonogramu sprawdza terminy przypomnien (min. 5).
    daas_scheduler_tick_sec: int = Field(default=60, ge=5, le=3600)
    daas_rate_limit_magic_email_per_min: int = Field(default=3, ge=0, description="POST /auth/magic-link, per e-mail")
    daas_rate_limit_magic_ip_per_min: int = Field(default=10, ge=0, description="POST /auth/magic-link, per IP")
    #: Odstepy miedzy ponowieniami dostawy webhooka, w sekundach (po jednej probie poczatkowej).
    daas_webhook_backoff_sec: str = "1,5,25"
    daas_sse_heartbeat_sec: float = Field(default=15.0, gt=0)
    #: Limity strumieni SSE (`GET .../jobs/{id}/events`): na podmiot (klucz/uzytkownik), globalnie i czas zycia jednego.
    daas_sse_max_per_principal: int = Field(default=5, ge=1, le=100)
    daas_sse_max_total: int = Field(default=100, ge=1, le=10000)
    daas_sse_max_seconds: int = Field(default=1800, ge=1, le=86400)
    # --- serwer MCP pod /mcp (dostep agentow AI; docs/MCP.md) ---
    #: `false` = trasy /mcp nie ma (404 jak nieznana trasa); sesja SDK nie startuje.
    daas_mcp_enabled: bool = True
    #: Limit ciala zadania POST /mcp (JSON-RPC); wieksze -> 413. Musi zmiescic `content` uploadu + escapowanie JSON.
    daas_mcp_max_body_bytes: int = Field(default=3 * 1024 * 1024, ge=64 * 1024, le=64 * 1024 * 1024)
    #: Limit tresci CSV narzedzia `upload_csv` (bajty UTF-8); wieksze -> blad narzedzia, bez zapisu na dysk.
    daas_mcp_max_upload_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=10 * 1024 * 1024)

    # CORS domyslnie wylaczony: n8n i klienci API wolaja serwer-serwer, przegladarka niepotrzebna.
    # Wlaczenie: CORS_ORIGINS='["https://prochpc.pl"]' w .env
    cors_origins: list[str] = Field(default_factory=list)

    # --- sekrety (opcjonalne) ---
    faceit_api_key: str | None = Field(default=None)
    faceit_player_nickname: str | None = Field(default=None)
    liquipedia_user_agent: str | None = Field(default=None)
    apify_token: str | None = Field(default=None)
    discord_webhook_url: str | None = Field(default=None)
    anthropic_api_key: str | None = Field(default=None, description="opcjonalnie: streszczenie LLM w raporcie")

    # --- storage ---
    duckdb_path: str = "runtime/daas.duckdb"
    reports_dir: str = "runtime/reports"
    fixtures_dir: str = "data/fixtures"

    # --- HTTP do źródeł zewnętrznych ---
    source_timeout_sec: float = 10.0

    @property
    def llm_api_key(self) -> str | None:
        """Klucz LLM tylko gdy wlaczono DAAS_LLM_NARRATIVE. Inaczej None: zadne dane nie wychodza do dostawcy."""
        if self.daas_llm_narrative and not _is_blank(self.anthropic_api_key):
            return (self.anthropic_api_key or "").strip()
        return None

    # ---------- ścieżki absolutne ----------
    def _abs(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def duckdb_file(self) -> Path:
        return self._abs(self.duckdb_path)

    @property
    def reports_path(self) -> Path:
        return self._abs(self.reports_dir)

    @property
    def fixtures_path(self) -> Path:
        return self._abs(self.fixtures_dir)

    @property
    def runtime_dir(self) -> Path:
        """Katalog runtime wdrozenia (obok pliku bazy): control.db, tenants/, outbox/."""
        return self.duckdb_file.parent

    @property
    def tenants_root(self) -> Path:
        return self.runtime_dir / "tenants"

    @property
    def outbox_dir(self) -> Path:
        return self.runtime_dir / "outbox"

    @property
    def uploads_path(self) -> Path:
        """Katalog uploadow tego wdrozenia/najemcy (obok katalogu raportow)."""
        return self.reports_path.parent / "uploads"

    @property
    def cloud(self) -> bool:
        return self.daas_mode == "cloud"

    @property
    def has_secret_key(self) -> bool:
        return not _is_blank(self.daas_secret_key) and len((self.daas_secret_key or "").strip()) >= MIN_SECRET_KEY_LENGTH

    @property
    def webhook_backoff(self) -> tuple[float, ...]:
        parts = [p.strip() for p in self.daas_webhook_backoff_sec.split(",") if p.strip()]
        try:
            return tuple(max(0.0, float(p)) for p in parts)
        except ValueError:
            return (1.0, 5.0, 25.0)

    @property
    def audit_log_path(self) -> Path:
        """Kanoniczna sciezka logu audytowego — jedno zrodlo prawdy dla wszystkich pisarzy.

        Kazdy call site (routes, runner, policy, upload_guard) czyta stad, zeby
        log nie rozwarstwil sie na kilka plikow w zaleznosci od miejsca wywolania.
        Katalog runtime wyznacza plik bazy — jeden tenant = jeden runtime = jeden audyt.
        """
        return self.duckdb_file.parent / AUDIT_LOG_FILENAME

    # ---------- auto-detekcja sekretów ----------
    @property
    def has_api_key(self) -> bool:
        return not _is_blank(self.daas_api_key)

    @property
    def has_faceit(self) -> bool:
        return not _is_blank(self.faceit_api_key)

    @property
    def has_liquipedia(self) -> bool:
        return not _is_blank(self.liquipedia_user_agent)

    @property
    def has_apify(self) -> bool:
        return not _is_blank(self.apify_token)

    @property
    def has_discord(self) -> bool:
        return not _is_blank(self.discord_webhook_url)

    def secrets_status(self) -> dict[str, bool]:
        """Mapa sekret -> czy obecny. Nigdy nie zwraca wartości sekretów."""
        return {
            "FACEIT_API_KEY": self.has_faceit,
            "LIQUIPEDIA_USER_AGENT": self.has_liquipedia,
            "APIFY_TOKEN": self.has_apify,
            "DISCORD_WEBHOOK_URL": self.has_discord,
            "ANTHROPIC_API_KEY": not _is_blank(self.anthropic_api_key),
        }

    def missing_secrets(self) -> list[str]:
        return [name for name, present in self.secrets_status().items() if not present]

    def ensure_dirs(self) -> None:
        self.duckdb_file.parent.mkdir(parents=True, exist_ok=True)
        self.reports_path.mkdir(parents=True, exist_ok=True)


def installed_version(fallback: str) -> str:
    """Wersja zainstalowanego pakietu `daas-engine` (metadane); `fallback` gdy pakiet nie jest zainstalowany."""
    try:
        return metadata.version("daas-engine")
    except metadata.PackageNotFoundError:
        return fallback


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    """Używane w testach po zmianie zmiennych środowiskowych."""
    get_settings.cache_clear()
