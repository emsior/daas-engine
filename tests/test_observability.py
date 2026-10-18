"""Request-id, logi JSON, naglowki bezpieczenstwa API, ksztalt bledow, asercja jednego workera."""
from __future__ import annotations

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.main import create_app
from tests.conftest import auth_headers

REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def test_every_response_carries_a_request_id(anon_client: TestClient):
    r = anon_client.get("/health")
    assert REQUEST_ID.match(r.headers["x-request-id"])
    assert anon_client.get("/health").headers["x-request-id"] != r.headers["x-request-id"]


def test_valid_incoming_request_id_is_kept(anon_client: TestClient):
    r = anon_client.get("/health", headers={"X-Request-ID": "trace-abc-12345"})
    assert r.headers["x-request-id"] == "trace-abc-12345"


@pytest.mark.parametrize("bad", ["x", "a b c d e f g h", "<script>alert(1)</script>", "a" * 200, "dobre\r\nX-Evil: 1"])
def test_invalid_incoming_request_id_is_replaced(anon_client: TestClient, bad: str):
    try:
        r = anon_client.get("/health", headers={"X-Request-ID": bad})
    except Exception:  # noqa: BLE001  klient HTTP moze odrzucic naglowek z CRLF jeszcze przed wyslaniem
        return
    assert r.headers["x-request-id"] != bad and REQUEST_ID.match(r.headers["x-request-id"])


def test_error_bodies_have_code_and_request_id(client: TestClient):
    r = client.get("/runs/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["detail"] == "run not found" and body["code"] == "not_found"
    assert body["request_id"] == r.headers["x-request-id"]


def test_validation_errors_have_code_and_request_id(client: TestClient):
    r = client.post("/run", json={})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error" and isinstance(body["detail"], list)
    assert body["request_id"] == r.headers["x-request-id"]


def test_unhandled_exception_returns_generic_500(settings, runner, monkeypatch: pytest.MonkeyPatch):
    app = create_app()
    with TestClient(app, headers=auth_headers(), raise_server_exceptions=False) as c:
        app.state.runner = runner

        def boom():
            raise RuntimeError("secret internals /srv/x")

        monkeypatch.setattr(runner.db, "table_counts", boom)
        r = c.get("/stats")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error" and "secret" not in r.text and body["request_id"]


def test_api_responses_carry_security_headers(client: TestClient):
    r = client.get("/pipelines")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "strict-transport-security" not in r.headers  # HSTS tylko za TLS


def test_report_csp_is_not_overridden_by_the_api_default(client: TestClient):
    client.post("/run", json={"pipeline": "cs2_demo"})
    r = client.get("/reports/cs2_demo/latest")
    assert "style-src 'unsafe-inline'" in r.headers["content-security-policy"]
    assert "x-frame-options" not in r.headers


def test_hsts_is_sent_only_over_https(settings, runner):
    app = create_app()
    with TestClient(app, base_url="https://testserver", headers=auth_headers()) as c:
        app.state.runner = runner
        r = c.get("/pipelines")
    assert "max-age=" in r.headers["strict-transport-security"]


def test_logs_are_json_lines_with_request_id(client: TestClient, capsys: pytest.CaptureFixture[str]):
    r = client.get("/pipelines", headers={"X-Request-ID": "trace-log-12345"})
    assert r.status_code == 200
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("{")]
    records = [json.loads(ln) for ln in lines]
    access = [x for x in records if x.get("logger") == "daas.access"]
    assert access and access[-1]["request_id"] == "trace-log-12345"
    assert access[-1]["status"] == 200 and access[-1]["path"] == "/pipelines"
    assert {"ts", "level", "logger", "msg"} <= set(access[-1])


def test_log_lines_never_contain_the_api_key(anon_client: TestClient, capsys: pytest.CaptureFixture[str]):
    anon_client.get("/pipelines", headers={"X-API-Key": "klucz-do-nie-logowania-123456"})
    err = capsys.readouterr().err
    assert "klucz-do-nie-logowania-123456" not in err


def test_exception_in_run_is_logged_with_request_id(client: TestClient, runner, monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(runner, "_run_cs2", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("detal-wewnetrzny")))
    r = client.post("/run", json={"pipeline": "cs2_demo"}, headers={"X-Request-ID": "trace-run-12345"})
    assert r.status_code == 200
    records = [json.loads(ln) for ln in capsys.readouterr().err.splitlines() if ln.startswith("{")]
    failed = [x for x in records if "run failed" in x["msg"]]
    assert failed and failed[0]["request_id"] == "trace-run-12345"
    assert "detal-wewnetrzny" in json.dumps(failed[0])


def test_configure_logging_is_idempotent():
    from app.core.observability import configure_logging

    configure_logging("INFO")
    configure_logging("INFO")
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_daas_json", False)]
    assert len(ours) == 1


# ---------------------------------------------------------------- H12: jeden worker
@pytest.mark.parametrize("var", ["WEB_CONCURRENCY", "UVICORN_WORKERS"])
def test_more_than_one_worker_refuses_to_start(monkeypatch: pytest.MonkeyPatch, var: str):
    monkeypatch.setenv(var, "4")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="worker"):
        create_app()


@pytest.mark.parametrize("value", ["1", "", "abc"])
def test_single_or_unset_worker_count_is_accepted(monkeypatch: pytest.MonkeyPatch, value: str):
    monkeypatch.setenv("WEB_CONCURRENCY", value)
    reset_settings_cache()
    create_app()
