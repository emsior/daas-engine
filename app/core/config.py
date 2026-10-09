"""Konfiguracja aplikacji (Pydantic Settings) z auto-detekcją braku sekretów.

Zasada: brak klucza NIGDY nie jest błędem. Pipeline przełącza się na fixtures
i oznacza wynik jako MOCK_DATA.
"""
from __future__ import annotations

from functools import lru_cache
from importlib import metadata
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

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
