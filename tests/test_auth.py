"""Uwierzytelnianie API: klucz w naglowku X-API-Key, fail-closed, trasy publiczne, redakcja /health.

Test po wszystkich trasach z `app.routes`: nowa trasa bez zaleznosci auth zawali ten plik.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.main import app as module_app
from app.main import create_app
from tests.conftest import API_KEY_HEADER, TEST_API_KEY, auth_headers, iter_api_routes

#: Trasy bez klucza: sonda zywotnosci/gotowosci, metryki (osobna kontrola: petla zwrotna albo token — zob.
#: test_metrics_ready.py) oraz dwa wejscia logowania magic linkiem (z natury publiczne; limity per e-mail/IP).
PUBLIC_GET_PATHS = {"/health", "/ready", "/api/v1/health", "/api/v1/ready"}
PUBLIC_PATHS = PUBLIC_GET_PATHS | {"/metrics", "/api/v1/auth/magic-link", "/api/v1/auth/magic-link/verify"}


def _route_cases() -> list[tuple[str, str]]:
    cases = []
    for route in iter_api_routes(module_app):
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            cases.append((method, path))
    return cases


ROUTE_CASES = _route_cases()
PROTECTED = [c for c in ROUTE_CASES if c[1] not in PUBLIC_PATHS]


def test_route_inventory_is_not_empty():
    # zabezpieczenie testu: parametryzacja musi cos obejmowac, w tym trasy publiczne
    assert len(PROTECTED) >= 9
    assert {p for _, p in ROUTE_CASES} - PUBLIC_PATHS == {p for _, p in PROTECTED}
    assert PUBLIC_PATHS - {"/metrics"} <= {p for _, p in ROUTE_CASES}


def _all_dependency_calls(dependant) -> set:
    calls = set()
    for sub in dependant.dependencies:
        calls.add(sub.call)
        calls |= _all_dependency_calls(sub)
    return calls


def test_module_app_exposes_no_unexpected_unprotected_routes():
    # kazda trasa spoza listy publicznej MUSI miec zaleznosc auth (statycznie, bez wywolan):
    # trasy legacy -> require_api_key, trasy /api/v1 -> get_principal (takze zagniezdzone w org_access)
    from app.api.v1.deps import get_principal
    from app.core.auth import require_api_key

    for route in iter_api_routes(module_app):
        if route.path in PUBLIC_PATHS:
            continue
        calls = _all_dependency_calls(route.dependant)
        required = get_principal if route.path.startswith("/api/v1/") else require_api_key
        assert required in calls, f"trasa bez auth: {route.path}"


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_protected_route_without_key_is_401(anon_client: TestClient, method: str, path: str):
    r = anon_client.request(method, path)
    assert r.status_code == 401, (method, path, r.status_code, r.text)
    assert r.headers["www-authenticate"] == "ApiKey"
    body = r.json()
    assert body["code"] == "unauthorized" and body["request_id"]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_protected_route_with_wrong_key_is_401(anon_client: TestClient, method: str, path: str):
    r = anon_client.request(method, path, headers={API_KEY_HEADER: "zly-klucz-0123456789abcdef"})
    assert r.status_code == 401, (method, path, r.status_code)


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_protected_route_with_valid_key_passes_auth(client: TestClient, method: str, path: str):
    r = client.request(method, path)
    assert r.status_code != 401, (method, path)


@pytest.mark.parametrize("path", sorted(PUBLIC_GET_PATHS))
def test_public_routes_need_no_key(anon_client: TestClient, path: str):
    assert anon_client.get(path).status_code == 200


@pytest.mark.parametrize("value", ["", " ", "null", "None", "changeme"])
def test_degenerate_key_values_are_rejected(anon_client: TestClient, value: str):
    r = anon_client.get("/pipelines", headers={API_KEY_HEADER: value})
    assert r.status_code == 401


def test_key_prefix_or_suffix_is_not_accepted(anon_client: TestClient):
    for bad in (TEST_API_KEY[:-1], TEST_API_KEY + "x", TEST_API_KEY.upper()):
        assert anon_client.get("/pipelines", headers={API_KEY_HEADER: bad}).status_code == 401


def test_key_comparison_is_constant_time_over_digests(anon_client: TestClient,
                                                      monkeypatch: pytest.MonkeyPatch):
    import app.core.auth as auth

    seen: list[tuple[int, int]] = []
    real = auth.hmac.compare_digest

    def spy(a, b):
        seen.append((len(a), len(b)))
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    anon_client.get("/pipelines", headers={API_KEY_HEADER: "a"})
    anon_client.get("/pipelines", headers={API_KEY_HEADER: "b" * 500})
    # porownanie zawsze na skrotach SHA-256 (32 B), niezaleznie od dlugosci podanego klucza
    assert seen == [(32, 32), (32, 32)]


def test_only_the_hash_of_the_key_is_kept_in_app_state():
    from app.core.auth import ApiKeyAuth

    s = Settings(_env_file=None, daas_api_key=TEST_API_KEY)
    a = ApiKeyAuth.from_settings(s)
    assert a.configured
    assert TEST_API_KEY not in repr(a)
    assert a.key_hash == hashlib.sha256(TEST_API_KEY.encode()).digest()


# ---------------------------------------------------------------- fail-closed
def test_no_key_configured_denies_everything(settings: Settings, runner, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DAAS_API_KEY", raising=False)
    reset_settings_cache()
    app = create_app()  # loopback: start dozwolony, ale nic nie jest otwarte
    with TestClient(app) as c:
        app.state.runner = runner
        for headers in ({}, {API_KEY_HEADER: ""}, {API_KEY_HEADER: TEST_API_KEY}):
            assert c.get("/pipelines", headers=headers).status_code == 401
            assert c.post("/run", json={"pipeline": "cs2_demo"}, headers=headers).status_code == 401
        assert c.get("/health").status_code == 200


def test_start_refused_without_key_on_non_loopback_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DAAS_API_KEY", raising=False)
    monkeypatch.setenv("APP_HOST", "0.0.0.0")  # noqa: S104
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="DAAS_API_KEY"):
        create_app()


def test_start_allowed_with_key_on_non_loopback_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_HOST", "0.0.0.0")  # noqa: S104
    reset_settings_cache()
    create_app()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_start_allowed_without_key_on_loopback(monkeypatch: pytest.MonkeyPatch, host: str):
    monkeypatch.delenv("DAAS_API_KEY", raising=False)
    monkeypatch.setenv("APP_HOST", host)
    reset_settings_cache()
    create_app()


def test_weak_key_refuses_start(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DAAS_API_KEY", "krotki")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="DAAS_API_KEY"):
        create_app()


# ---------------------------------------------------------------- /health, /ready, docs
def test_health_is_minimal_and_has_no_paths_or_secrets(anon_client: TestClient):
    r = anon_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "app", "version", "mode", "timestamp"}
    assert body["status"] == "ok" and body["mode"] == "selfhost"
    text = r.text
    for needle in ("duckdb", "secrets", "FACEIT", "ANTHROPIC", "/tmp", "runtime", TEST_API_KEY):
        assert needle not in text


def test_ready_reports_ok_and_leaks_nothing(anon_client: TestClient):
    r = anon_client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_ready_is_503_when_database_is_unavailable(anon_client: TestClient, runner,
                                                   monkeypatch: pytest.MonkeyPatch):
    def boom(*_a, **_k):
        raise OSError("/srv/secret/path is broken")

    monkeypatch.setattr(runner.db, "table_counts", boom)
    r = anon_client.get("/ready")
    assert r.status_code == 503
    assert "secret" not in r.text and r.json()["status"] == "not_ready"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_absent_outside_dev(monkeypatch: pytest.MonkeyPatch, path: str):
    monkeypatch.setenv("APP_ENV", "prod")
    reset_settings_cache()
    with TestClient(create_app(), headers=auth_headers()) as c:
        assert c.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_served_in_dev(monkeypatch: pytest.MonkeyPatch, path: str):
    monkeypatch.setenv("APP_ENV", "dev")
    reset_settings_cache()
    with TestClient(create_app()) as c:
        assert c.get(path).status_code == 200


def test_default_environment_is_not_dev(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert Settings(_env_file=None).app_env != "dev"


def test_openapi_declares_the_api_key_scheme(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "dev")
    reset_settings_cache()
    with TestClient(create_app()) as c:
        schemes = c.get("/openapi.json").json()["components"]["securitySchemes"]
    assert any(s.get("name") == API_KEY_HEADER and s.get("in") == "header" for s in schemes.values())


# ---------------------------------------------------------------- audyt odmowy
def _audit(settings: Settings) -> list[dict]:
    p = settings.audit_log_path
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def test_denied_request_is_audited_without_the_presented_key(anon_client: TestClient, runner,
                                                             settings: Settings):
    presented = "zly-klucz-SEKRET-0123456789"
    anon_client.get("/pipelines", headers={API_KEY_HEADER: presented})
    anon_client.post("/run", json={"pipeline": "cs2_demo"})
    events = [e for e in _audit(runner.settings) if e.get("action") == "auth_denied"]
    assert [e["reason_code"] for e in events] == ["auth_invalid", "auth_missing"]
    assert all(e["decision"] == "deny" for e in events)
    assert events[0]["route"] == "/pipelines" and events[1]["route"] == "/run"
    raw = Path(runner.settings.audit_log_path).read_text(encoding="utf-8")
    assert presented not in raw and TEST_API_KEY not in raw


def test_authorized_request_is_not_an_auth_denied_event(client: TestClient, runner):
    client.get("/pipelines")
    assert not [e for e in _audit(runner.settings) if e.get("action") == "auth_denied"]
