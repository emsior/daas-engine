"""Konfiguracja aplikacji (Pydantic Settings) z auto-detekcją braku sekretów.

Zasada: brak klucza NIGDY nie jest błędem. Pipeline przełącza się na fixtures
i oznacza wynik jako MOCK_DATA.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    app_env: str = "dev"
    app_name: str = "DaaS Engine"
    app_version: str = "0.3.0"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    daas_policy_enforce: bool = Field(default=False, description="Wymuszenie sprawdzania uprawnień policy w runtime")

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

    # ---------- auto-detekcja sekretów ----------
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    """Używane w testach po zmianie zmiennych środowiskowych."""
    get_settings.cache_clear()
