"""Testy: oba pipeline'y w trybie mock, zapis do tymczasowej bazy DuckDB, kody odpowiedzi FastAPI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.core.models import DataStatus, RunRequest, RunStatus
from app.pipelines.runner import PipelineRunner
from app.storage.duckdb_client import DuckDBClient
from app.transforms.cs2_transform import compute_form_score, transform_cs2
from app.transforms.ecommerce_transform import transform_ecommerce

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    # Wyczyść sekrety -> wymuszamy MOCK_DATA niezależnie od .env dewelopera
    for key in ("FACEIT_API_KEY", "FACEIT_PLAYER_NICKNAME", "LIQUIPEDIA_USER_AGENT", "APIFY_TOKEN",
                "DISCORD_WEBHOOK_URL", "APIFY_DATASET_ID", "ECOMMERCE_CSV_PATH", "ANTHROPIC_API_KEY"):
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
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        # podmieniamy runner na testowy (tymczasowa baza)
        app.state.runner = runner
        yield c


# ---------------------------------------------------------------------------
# Konfiguracja / sekrety
# ---------------------------------------------------------------------------
def test_missing_secrets_detected(settings: Settings):
    assert settings.has_faceit is False
    assert settings.has_apify is False
    assert set(settings.missing_secrets()) == {"FACEIT_API_KEY", "LIQUIPEDIA_USER_AGENT", "APIFY_TOKEN",
                                               "DISCORD_WEBHOOK_URL", "ANTHROPIC_API_KEY"}


# ---------------------------------------------------------------------------
# Pipeline'y (mock mode)
# ---------------------------------------------------------------------------
def test_cs2_pipeline_mock(runner: PipelineRunner, settings: Settings):
    res = runner.run(RunRequest(pipeline="cs2_demo"))
    assert res.run_status == RunStatus.SUCCESS
    assert res.data_status == DataStatus.MOCK_DATA
    assert res.records_processed == 10
    assert res.execution_time_sec >= 0
    assert Path(res.report_path).exists() and res.report_path.endswith(".md")
    assert Path(res.report_json_path).exists()
    payload = json.loads(Path(res.report_json_path).read_text(encoding="utf-8"))
    assert payload["metrics"]["matches"] == 10
    assert 0 <= payload["metrics"]["form_score"] <= 100
    assert "PRAKTYCZNE WNIOSKI" in Path(res.report_path).read_text(encoding="utf-8")


def test_ecommerce_pipeline_mock(runner: PipelineRunner):
    res = runner.run(RunRequest(pipeline="ecommerce_demo"))
    assert res.run_status == RunStatus.SUCCESS
    assert res.data_status == DataStatus.MOCK_DATA
    assert res.records_processed == 20
    m = res.metrics
    assert m["orders_total"] == 20
    assert m["loss_orders_count"] == 3
    assert m["revenue_total"] > 0 and 0 < m["margin_pct"] < 100
    payload = json.loads(Path(res.report_json_path).read_text(encoding="utf-8"))
    types = {a["type"] for a in payload["metrics"]["anomalies"]}
    assert "NEGATIVE_PROFIT" in types
    assert payload["metrics"]["top_categories"][0]["category"] == "Electronics"


def test_force_mock_flag(runner: PipelineRunner):
    res = runner.run(RunRequest(pipeline="cs2_demo", force_mock=True))
    assert res.data_status == DataStatus.MOCK_DATA
    assert "force_mock" in (res.source_detail or "")


# ---------------------------------------------------------------------------
# DuckDB — zapis w tymczasowej bazie, idempotencja
# ---------------------------------------------------------------------------
def test_duckdb_persistence(runner: PipelineRunner, settings: Settings):
    r1 = runner.run(RunRequest(pipeline="cs2_demo"))
    r2 = runner.run(RunRequest(pipeline="ecommerce_demo"))
    counts = runner.db.table_counts()
    assert counts == {"runs": 2, "cs2_matches": 10, "ecommerce_orders": 20}
    assert settings.duckdb_file.exists()

    latest = runner.db.latest_run()
    assert latest["run_id"] == r2.run_id
    assert runner.db.latest_run("cs2_demo")["run_id"] == r1.run_id

    df = runner.db.read_cs2_matches(r1.run_id)
    assert len(df) == 10 and set(df["data_status"]) == {"MOCK_DATA"}

    # idempotencja: ponowny zapis tych samych kluczy nie duplikuje
    runner.db.upsert_run(r1.model_dump())
    assert runner.db.table_counts()["runs"] == 2


# ---------------------------------------------------------------------------
# Transforms — czysta logika
# ---------------------------------------------------------------------------
def test_transforms_empty_input():
    assert transform_cs2([]).matches == 0
    assert transform_ecommerce([]).orders_total == 0


def test_form_score_rewards_recent_wins():
    import pandas as pd

    base = dict(kills=20, deaths=20, adr=75.0, rating=1.0, map="de_mirage", match_id="x")
    good = pd.DataFrame([{**base, "played_at": f"2026-01-0{i+1}", "result": "WIN"} for i in range(5)])
    bad = pd.DataFrame([{**base, "played_at": f"2026-01-0{i+1}", "result": "LOSS"} for i in range(5)])
    assert compute_form_score(good) > compute_form_score(bad)


# ---------------------------------------------------------------------------
# FastAPI — kody odpowiedzi
# ---------------------------------------------------------------------------
def test_api_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["secrets"]["FACEIT_API_KEY"] is False


def test_api_pipelines(client: TestClient):
    r = client.get("/pipelines")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert names == {"cs2_demo", "ecommerce_demo"}
    assert all(p["expected_data_status"] == "MOCK_DATA" for p in r.json())


def test_api_run_and_latest(client: TestClient):
    assert client.get("/runs/latest").status_code == 404

    for pipeline in ("cs2_demo", "ecommerce_demo"):
        r = client.post("/run", json={"pipeline": pipeline})
        assert r.status_code == 200, r.text
        body = r.json()
        for key in ("run_id", "pipeline", "run_status", "data_status", "records_processed",
                    "report_path", "execution_time_sec"):
            assert key in body
        assert body["data_status"] == "MOCK_DATA"
        assert body["run_status"] == "SUCCESS"

    latest = client.get("/runs/latest")
    assert latest.status_code == 200
    assert latest.json()["pipeline"] == "ecommerce_demo"
    assert client.get("/runs/latest", params={"pipeline": "cs2_demo"}).json()["pipeline"] == "cs2_demo"
    assert client.get(f"/runs/{latest.json()['run_id']}").status_code == 200
    assert client.get("/runs/does-not-exist").status_code == 404
    assert len(client.get("/runs").json()) == 2


def test_api_run_validation(client: TestClient):
    assert client.post("/run", json={"pipeline": "nope"}).status_code == 422
    assert client.post("/run", json={}).status_code == 422
