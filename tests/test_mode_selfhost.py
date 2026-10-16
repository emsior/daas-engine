"""Tryby DAAS_MODE: selfhost (org `default`, trasy legacy) i cloud (tylko /api/v1, legacy = 404)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.control import models
from app.control.db import create_engine, make_sessionmaker
from app.core.config import Settings, reset_settings_cache
from app.main import create_app
from tests.conftest import API_KEY_HEADER, TEST_API_KEY, TEST_SECRET_KEY, auth_headers

LEGACY_ROUTES = [
    ("GET", "/pipelines"), ("POST", "/run"), ("GET", "/runs"), ("GET", "/runs/latest"), ("GET", "/runs/x"),
    ("GET", "/stats"), ("POST", "/upload"), ("GET", "/reports/ecommerce_demo/latest"), ("GET", "/reports/run/x"),
]


def _orgs(settings: Settings) -> list[models.Org]:
    async def go():
        engine = create_engine(settings)
        try:
            async with make_sessionmaker(engine)() as db:
                return list((await db.execute(select(models.Org))).scalars())
        finally:
            await engine.dispose()

    return asyncio.run(go())


# ---------------------------------------------------------------- selfhost
def test_default_mode_is_selfhost():
    assert Settings(_env_file=None).daas_mode == "selfhost"


def test_selfhost_creates_default_org_at_startup(client: TestClient, settings: Settings):
    orgs = _orgs(Settings(_env_file=None))
    assert [(o.id, o.plan, o.slug) for o in orgs] == [("default", "selfhost", "default")]


def test_selfhost_default_org_survives_restart(settings: Settings):
    for _ in range(2):
        with TestClient(create_app()):
            pass
    assert len(_orgs(Settings(_env_file=None))) == 1


def test_selfhost_tenant_directory_layout(client: TestClient, settings: Settings):
    root = settings.tenants_root / "default"
    assert (root / "uploads").is_dir() and (root / "reports").is_dir() and (root / "daas.duckdb").is_file()


def test_selfhost_runner_is_pinned_to_default_tenant(settings: Settings):
    with TestClient(create_app()) as c:
        runner = c.app.state.runner
        assert runner.settings.duckdb_file == (settings.tenants_root / "default" / "daas.duckdb").resolve()
        assert runner.settings.uploads_path == (settings.tenants_root / "default" / "uploads").resolve()
        assert runner.settings.audit_log_path.parent == runner.settings.duckdb_file.parent


@pytest.mark.parametrize(("method", "path"), LEGACY_ROUTES)
def test_selfhost_legacy_routes_exist_and_are_marked_deprecated(client: TestClient, method: str, path: str):
    r = client.request(method, path)
    assert r.status_code != 404 or r.json().get("detail") in {"run not found", "no runs yet", "upload not found",
                                                              "no report yet — run the pipeline first"}
    assert r.headers["deprecation"] == "true"
    assert 'rel="successor-version"' in r.headers["link"]


def test_selfhost_legacy_run_flow_still_works(client: TestClient):
    body = client.post("/run", json={"pipeline": "ecommerce_demo"}).json()
    assert body["run_status"] in {"SUCCESS", "PARTIAL"} and body["data_status"] == "MOCK_DATA"


@pytest.mark.parametrize("path", ["/health", "/ready"])
def test_public_routes_are_not_marked_deprecated(anon_client: TestClient, path: str):
    assert "deprecation" not in anon_client.get(path).headers


def test_selfhost_health_reports_mode(anon_client: TestClient):
    assert anon_client.get("/health").json()["mode"] == "selfhost"


def test_selfhost_env_key_still_authenticates(anon_client: TestClient):
    assert anon_client.get("/pipelines", headers={API_KEY_HEADER: TEST_API_KEY}).status_code == 200
    assert anon_client.get("/pipelines").status_code == 401


def test_selfhost_secret_key_is_optional(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DAAS_SECRET_KEY")
    reset_settings_cache()
    create_app()


def test_selfhost_short_secret_key_refuses_start(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DAAS_SECRET_KEY", "too-short")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="DAAS_SECRET_KEY"):
        create_app()


def test_selfhost_warns_about_unmigrated_runtime(settings: Settings, caplog: pytest.LogCaptureFixture):
    settings.duckdb_file.write_bytes(b"")  # plik bazy sprzed 2.0 obok nowego katalogu najemcow
    with caplog.at_level(logging.WARNING, logger="daas"), TestClient(create_app()):
        pass
    assert any("migrate-runtime" in r.getMessage() for r in caplog.records)


def test_selfhost_does_not_warn_without_legacy_database(settings: Settings, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING, logger="daas"), TestClient(create_app()):
        pass
    assert not [r for r in caplog.records if "migrate-runtime" in r.getMessage()]


# ---------------------------------------------------------------- cloud
def test_cloud_mode_value(cloud_settings: Settings):
    assert cloud_settings.daas_mode == "cloud" and cloud_settings.cloud


@pytest.mark.parametrize(("method", "path"), LEGACY_ROUTES)
def test_cloud_legacy_routes_are_404_even_with_a_valid_key(cloud_client: TestClient, method: str, path: str):
    for headers in ({}, auth_headers(), {API_KEY_HEADER: "daas_00000000_" + "a" * 43}):
        assert cloud_client.request(method, path, headers=headers).status_code == 404


def test_cloud_health_and_ready_are_public(cloud_client: TestClient):
    health = cloud_client.get("/health").json()
    assert health["mode"] == "cloud" and health["status"] == "ok"
    assert cloud_client.get("/ready").json() == {"status": "ready"}


def test_cloud_has_no_default_org_and_no_default_runner(cloud_client: TestClient, cloud_settings: Settings):
    assert _orgs(cloud_settings) == []
    assert cloud_client.app.state.runner is None
    assert not (cloud_settings.tenants_root / "default").exists()


def test_cloud_requires_secret_key(cloud_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DAAS_SECRET_KEY")
    with pytest.raises(RuntimeError, match="DAAS_SECRET_KEY is required"):
        create_app(Settings(_env_file=None))


def test_cloud_rejects_short_secret_key(cloud_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DAAS_SECRET_KEY", "x" * 31)
    with pytest.raises(RuntimeError, match="DAAS_SECRET_KEY"):
        create_app(Settings(_env_file=None))


def test_cloud_needs_no_instance_api_key_on_public_host(cloud_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("DAAS_SECRET_KEY", TEST_SECRET_KEY)
    create_app(Settings(_env_file=None))


def test_cloud_responses_still_carry_request_id_and_security_headers(cloud_client: TestClient):
    r = cloud_client.get("/health")
    assert r.headers["x-request-id"] and r.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------- /ready
def test_ready_is_503_when_control_database_fails(anon_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    class Broken:
        async def __aenter__(self):
            raise OSError("/srv/secret/control.db is gone")

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(anon_client.app.state, "db_sessionmaker", lambda: Broken())
    r = anon_client.get("/ready")
    assert r.status_code == 503 and r.json() == {"status": "not_ready"} and "secret" not in r.text


def test_ready_is_503_when_runtime_dir_is_not_writable(anon_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import app.api.routes as routes

    def deny(_directory: Path) -> None:
        raise PermissionError("/srv/runtime is read-only")

    monkeypatch.setattr(routes, "_probe_runtime_dir", deny)
    r = anon_client.get("/ready")
    assert r.status_code == 503 and "read-only" not in r.text


def test_ready_probe_leaves_no_file_behind(anon_client: TestClient, settings: Settings):
    anon_client.get("/ready")
    assert not list(settings.runtime_dir.glob(".ready-*"))
