"""Wspolne fixture'y testow API.

Jedno miejsce na: izolowany `Settings`, `PipelineRunner` na tymczasowej bazie oraz klienta HTTP
z naglowkiem klucza API. Testy, ktore buduja wlasnego `TestClient`, uzywaja `auth_headers()`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.pipelines.runner import PipelineRunner
from app.storage.duckdb_client import DuckDBClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Klucz uzywany wylacznie w testach (syntetyczny, >= 16 znakow).
TEST_API_KEY = "test-key-0123456789abcdef-synthetic"
API_KEY_HEADER = "X-API-Key"

# Import `app.main` tworzy aplikacje na poziomie modulu; klucz musi istniec wczesniej.
os.environ.setdefault("DAAS_API_KEY", TEST_API_KEY)

_SECRET_ENV_KEYS = (
    "FACEIT_API_KEY", "FACEIT_PLAYER_NICKNAME", "LIQUIPEDIA_USER_AGENT", "APIFY_TOKEN",
    "DISCORD_WEBHOOK_URL", "APIFY_DATASET_ID", "ECOMMERCE_CSV_PATH", "ANTHROPIC_API_KEY",
)


def auth_headers(key: str = TEST_API_KEY) -> dict[str, str]:
    """Naglowki uwierzytelniajace dla `TestClient(app, headers=auth_headers())`."""
    return {API_KEY_HEADER: key}


@pytest.fixture(autouse=True)
def _api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kazdy test dostaje klucz w srodowisku; test negatywny nadpisuje go sam."""
    monkeypatch.setenv("DAAS_API_KEY", TEST_API_KEY)
    reset_settings_cache()


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    # Wyczysc sekrety -> wymuszamy MOCK_DATA niezaleznie od .env developera
    for key in _SECRET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "test.duckdb"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("FIXTURES_DIR", str(PROJECT_ROOT / "data" / "fixtures"))
    reset_settings_cache()
    s = Settings(_env_file=None)
    s.ensure_dirs()
    yield s
    reset_settings_cache()


@pytest.fixture()
def runner(settings: Settings) -> PipelineRunner:
    return PipelineRunner(settings=settings, db=DuckDBClient(settings.duckdb_file))


@pytest.fixture()
def client(settings: Settings, runner: PipelineRunner) -> TestClient:
    """Klient z naglowkiem klucza; runner podmieniony na testowy (tymczasowa baza)."""
    from app.main import create_app

    app = create_app()
    with TestClient(app, headers=auth_headers()) as c:
        app.state.runner = runner
        yield c
