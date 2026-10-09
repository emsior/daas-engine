"""Redakcja odpowiedzi API: zadnych sciezek serwera, zadnych tresci wyjatkow, szczegoly tylko w logu.

Wewnetrzny kontrakt runnera (RunResult z `error` i sciezkami) zostaje — redakcja dziala na krawedzi API.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.upload_guard import AUDIT_ALLOWED_KEYS
from app.pipelines.runner import PipelineRunner
from tests.conftest import PROJECT_ROOT

CSV = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()
ABS_PATH = re.compile(r"(?:^|[\"'\s=:(])(/(?:tmp|home|srv|var|root|usr|etc)/[^\s\"']+|[A-Za-z]:[\\/][^\s\"']+)")


def _no_abs_paths(text: str, settings: Settings) -> None:
    assert str(settings.reports_path.parent) not in text
    assert str(settings.duckdb_file) not in text
    assert str(PROJECT_ROOT) not in text
    assert not ABS_PATH.search(text), ABS_PATH.search(text).group(0)


def _audit(runner: PipelineRunner) -> list[dict]:
    p = runner.settings.audit_log_path
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def _upload(client: TestClient) -> dict:
    r = client.post("/upload", files={"file": ("dane.csv", io.BytesIO(CSV), "text/csv")},
                    data={"client_name": "Sklep Demo"})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- /upload
def test_upload_response_has_no_server_path(client: TestClient, settings: Settings):
    up = _upload(client)
    assert "source_path" not in up
    assert up["run_payload"].keys() == {"pipeline", "upload_id", "client_name"}
    assert up["run_payload"]["upload_id"] == up["upload_id"]
    _no_abs_paths(json.dumps(up), settings)


def test_run_by_upload_id_end_to_end(client: TestClient):
    up = _upload(client)
    run = client.post("/run", json=up["run_payload"])
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["run_status"] == "SUCCESS" and body["data_status"] == "LIVE_DATA"
    assert body["client_name"] == "Sklep Demo"


def test_run_with_unknown_upload_id_is_404(client: TestClient):
    r = client.post("/run", json={"pipeline": "ecommerce_demo", "upload_id": "0" * 32})
    assert r.status_code == 404
    assert r.json()["detail"] == "upload not found"


@pytest.mark.parametrize("bad", ["../../etc/passwd", "ABC", "0" * 31, "g" * 32, "0" * 33])
def test_run_rejects_malformed_upload_id(client: TestClient, bad: str):
    assert client.post("/run", json={"pipeline": "ecommerce_demo", "upload_id": bad}).status_code == 422


def test_run_rejects_both_upload_id_and_source_path(client: TestClient):
    r = client.post("/run", json={"pipeline": "ecommerce_demo", "upload_id": "0" * 32, "source_path": "x"})
    assert r.status_code == 422


# ---------------------------------------------------------------- /run, /runs
def test_run_result_has_links_instead_of_paths(client: TestClient, settings: Settings):
    r = client.post("/run", json={"pipeline": "ecommerce_demo"})
    assert r.status_code == 200
    body = r.json()
    rid = body["run_id"]
    assert body["report_path"] == f"/reports/run/{rid}?fmt=md"
    assert body["report_json_path"] == f"/reports/run/{rid}?fmt=json"
    assert body["report_html_path"] == f"/reports/run/{rid}?fmt=html"
    _no_abs_paths(r.text, settings)
    for key, fmt in (("report_path", "md"), ("report_json_path", "json"), ("report_html_path", "html")):
        assert client.get(body[key]).status_code == 200, fmt


def test_run_history_endpoints_are_redacted_too(client: TestClient, settings: Settings):
    rid = client.post("/run", json={"pipeline": "cs2_demo"}).json()["run_id"]
    for url in ("/runs/latest", "/runs", f"/runs/{rid}"):
        r = client.get(url)
        assert r.status_code == 200
        _no_abs_paths(r.text, settings)
    assert client.get(f"/runs/{rid}").json()["report_html_path"].startswith("/reports/run/")


def test_internal_runner_contract_keeps_absolute_paths(runner: PipelineRunner):
    from app.core.models import RunRequest

    res = runner.run(RunRequest(pipeline="cs2_demo"))
    assert Path(res.report_path).is_absolute() and Path(res.report_path).exists()


def test_failed_run_error_is_a_code_with_a_generic_message(client: TestClient, settings: Settings):
    r = client.post("/run", json={"pipeline": "ecommerce_demo", "source_path": "/etc/passwd"})
    assert r.status_code == 200
    body = r.json()
    assert body["run_status"] == "FAILED"
    assert body["error"].startswith("SOURCE_UNAVAILABLE: ")
    assert body["run_id"] in body["error"]
    assert "/etc/passwd" not in r.text and "outside" not in r.text
    _no_abs_paths(r.text, settings)
    stored = client.get(f"/runs/{body['run_id']}").json()
    assert stored["error"] == body["error"]


def test_unexpected_exception_text_never_reaches_the_response(client: TestClient, runner: PipelineRunner,
                                                               monkeypatch: pytest.MonkeyPatch,
                                                               caplog: pytest.LogCaptureFixture):
    def boom(*_a, **_k):
        raise RuntimeError("connection to /srv/secret/db failed password=hunter2")

    monkeypatch.setattr(runner, "_run_cs2", boom)
    with caplog.at_level("ERROR"):
        r = client.post("/run", json={"pipeline": "cs2_demo"})
    body = r.json()
    assert body["run_status"] == "FAILED" and body["error"].startswith("RUN_FAILED: ")
    assert "hunter2" not in r.text and "/srv/secret" not in r.text
    # szczegol zostaje po stronie serwera, z run_id do korelacji
    assert any("hunter2" in rec.getMessage() or "hunter2" in str(rec.exc_info) for rec in caplog.records)
    assert any(body["run_id"] in rec.getMessage() for rec in caplog.records)


def test_source_detail_never_contains_paths(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    from app.core.models import RunRequest
    from app.storage.duckdb_client import DuckDBClient

    missing = settings.reports_path.parent / "brak-fixtures"
    monkeypatch.setenv("FIXTURES_DIR", str(missing))
    monkeypatch.setenv("ECOMMERCE_CSV_PATH", str(settings.reports_path.parent / "nie-ma.csv"))
    s2 = Settings(_env_file=None)
    r2 = PipelineRunner(settings=s2, db=DuckDBClient(s2.duckdb_file))
    for pipeline in ("cs2_demo", "ecommerce_demo", "uksc_evidence"):
        res = r2.run(RunRequest(pipeline=pipeline))
        assert "brak-fixtures" not in (res.source_detail or "") + (res.error or ""), pipeline
        assert "nie-ma.csv" not in (res.source_detail or "") + (res.error or ""), pipeline


# ---------------------------------------------------------------- audyt /run
def test_run_is_audited_with_allowed_keys_only(client: TestClient, runner: PipelineRunner):
    rid = client.post("/run", json={"pipeline": "cs2_demo", "client_name": "Tajna Nazwa"}).json()["run_id"]
    events = [e for e in _audit(runner) if e.get("action") == "run"]
    assert len(events) == 1
    ev = events[0]
    assert ev["pipeline"] == "cs2_demo" and ev["run_id"] == rid
    assert ev["decision"] == "allow" and ev["reason_code"] == "ok"
    assert ev["request_id"]
    assert set(ev) <= AUDIT_ALLOWED_KEYS
    assert "Tajna Nazwa" not in json.dumps(ev)  # nazwa klienta to tresc od klienta, nie trafia do audytu


def test_failed_run_is_audited_as_run_failed(client: TestClient, runner: PipelineRunner):
    client.post("/run", json={"pipeline": "ecommerce_demo", "source_path": "/etc/passwd"})
    ev = [e for e in _audit(runner) if e.get("action") == "run"][-1]
    assert ev["reason_code"] == "run_failed" and ev["decision"] == "allow"


def test_upload_events_keep_their_shape(client: TestClient, runner: PipelineRunner):
    _upload(client)
    ev = [e for e in _audit(runner) if e.get("action") == "upload"][-1]
    assert ev["decision"] == "allow" and set(ev) <= AUDIT_ALLOWED_KEYS
    assert ev["request_id"]
