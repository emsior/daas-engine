"""Wspolne fixture'y testow API.

Jedno miejsce na: izolowany `Settings`, `PipelineRunner` na tymczasowej bazie oraz klienta HTTP
z naglowkiem klucza API. Testy, ktore buduja wlasnego `TestClient`, uzywaja `auth_headers()`.
"""
from __future__ import annotations

import os
import shutil
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

#: Sekret podpisu sesji uzywany wylacznie w testach (>= 32 znaki).
TEST_SECRET_KEY = "test-secret-0123456789abcdef0123456789abcdef-synthetic"

# Import `app.main` tworzy aplikacje na poziomie modulu; klucz musi istniec wczesniej.
os.environ.setdefault("DAAS_API_KEY", TEST_API_KEY)

_SECRET_ENV_KEYS = (
    "FACEIT_API_KEY", "FACEIT_PLAYER_NICKNAME", "LIQUIPEDIA_USER_AGENT", "APIFY_TOKEN",
    "DISCORD_WEBHOOK_URL", "APIFY_DATASET_ID", "ECOMMERCE_CSV_PATH", "ANTHROPIC_API_KEY",
)


def iter_api_routes(app):
    """Wszystkie trasy aplikacji z PELNA sciezka (z prefiksami dolaczonych routerow) i `dependant` obejmujacym
    zaleznosci poziomu routera. FastAPI >= 0.13x trzyma dolaczone routery leniwie (`_IncludedRouter`), a
    sciezki z prefiksem sa dopiero w `effective_candidates()`; obiekty maja `.path`, `.methods`, `.dependant`."""

    def walk(candidates):
        for route in candidates:
            if hasattr(route, "effective_candidates"):
                yield from walk(route.effective_candidates())
            elif getattr(route, "dependant", None) is not None and getattr(route, "methods", None):
                yield route

    yield from walk(app.routes)


def default_tenant(settings: Settings):
    """Sciezki organizacji `default` (tryb selfhost) dla ustawien testu: tenants/default/{uploads,reports,audit}."""
    from app.control.tenancy import TenantPaths

    return TenantPaths.for_org(settings, "default")


def auth_headers(key: str = TEST_API_KEY) -> dict[str, str]:
    """Naglowki uwierzytelniajace dla `TestClient(app, headers=auth_headers())`."""
    return {API_KEY_HEADER: key}


@pytest.fixture(scope="session")
def _control_db_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Baza control plane po `alembic upgrade head`, zrobiona raz na sesje i kopiowana do kazdego testu
    (start aplikacji nie musi wtedy uruchamiac migracji od zera przy kazdym tescie)."""
    from app.control.db import upgrade_sync

    path = tmp_path_factory.mktemp("control_template") / "template.db"
    upgrade_sync(Settings(_env_file=None, daas_control_db_url=f"sqlite+aiosqlite:///{path}"))
    return path


@pytest.fixture(autouse=True)
def _api_key_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _control_db_template: Path) -> None:
    """Kazdy test dostaje klucz, sekret sesji i wlasna baze control plane w srodowisku;
    test negatywny nadpisuje je sam. Domyslna baza (`runtime/control.db`) nie jest nigdy dotykana."""
    monkeypatch.setenv("DAAS_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("DAAS_SECRET_KEY", TEST_SECRET_KEY)
    shutil.copy(_control_db_template, tmp_path / "control.db")
    monkeypatch.setenv("DAAS_CONTROL_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
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


@pytest.fixture()
def anon_client(settings: Settings, runner: PipelineRunner) -> TestClient:
    """Klient BEZ naglowka klucza (testy odmowy). Runner testowy jak w `client`."""
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        app.state.runner = runner
        yield c


# --------------------------------------------------------------------------- tryb cloud
@pytest.fixture()
def cloud_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Ustawienia trybu `cloud` na katalogu tymczasowym: runtime/ w tmp_path, e-maile do runtime/outbox/."""
    for key in _SECRET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DAAS_MODE", "cloud")
    monkeypatch.delenv("DAAS_API_KEY", raising=False)
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "runtime" / "daas.duckdb"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "runtime" / "reports"))
    monkeypatch.setenv("FIXTURES_DIR", str(PROJECT_ROOT / "data" / "fixtures"))
    monkeypatch.setenv("DAAS_COCKPIT_ORIGIN", "https://cockpit.example.test")
    reset_settings_cache()
    s = Settings(_env_file=None)
    s.ensure_dirs()
    yield s
    reset_settings_cache()


@pytest.fixture()
def cloud_client(cloud_settings: Settings) -> TestClient:
    """Klient trybu `cloud` po https (cookie sesji ma atrybut Secure)."""
    from app.main import create_app

    app = create_app(cloud_settings)
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture()
def cloud_factory(cloud_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    """Fabryka klientow cloud z nadpisanymi zmiennymi srodowiska: `make(DAAS_UPLOAD_QUOTA_BYTES=10)`."""
    from contextlib import ExitStack

    from app.main import create_app

    stack = ExitStack()

    def make(**env) -> tuple[TestClient, Settings]:
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))
        reset_settings_cache()
        s = Settings(_env_file=None)
        app = create_app(s)
        return stack.enter_context(TestClient(app, base_url="https://testserver")), s

    yield make
    stack.close()
