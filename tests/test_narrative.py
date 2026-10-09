"""Streszczenie LLM jest opt-in (DAAS_LLM_NARRATIVE) i nie wysyla identyfikatorow zamowien ani nazw produktow."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.core.models import RunRequest
from app.pipelines.runner import PipelineRunner
from app.reporting import narrative as nar
from app.sources.csv_mapper import map_dataframe, read_client_csv
from app.storage.duckdb_client import DuckDBClient
from app.transforms.ecommerce_transform import transform_ecommerce
from tests.conftest import PROJECT_ROOT

FIXTURE = PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv"


class _Boom(AssertionError):
    """Nieoczekiwane wywolanie HTTP."""


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"content": [{"type": "text", "text": "Streszczenie testowe od modelu."}]}


@pytest.fixture()
def http_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Przechwytuje kazde `httpx.post` z modulu narracji. Domyslnie odpowiada poprawnie."""
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _FakeResponse()

    monkeypatch.setattr(nar.httpx, "post", fake_post)
    return calls


@pytest.fixture()
def no_http(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):
        raise _Boom("wywolanie HTTP bez zgody (DAAS_LLM_NARRATIVE=false)")

    monkeypatch.setattr(nar.httpx, "post", boom)


def _metrics():
    mapped, _ = map_dataframe(read_client_csv(str(FIXTURE)))
    return transform_ecommerce(mapped.to_dict(orient="records"))


# ---------------------------------------------------------------- ustawienia
def test_llm_narrative_is_off_by_default():
    assert Settings(_env_file=None).daas_llm_narrative is False


def test_key_alone_does_not_enable_llm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    s = Settings(_env_file=None)
    assert s.llm_api_key is None


def test_flag_without_key_does_not_enable_llm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert Settings(_env_file=None, daas_llm_narrative=True).llm_api_key is None


def test_flag_and_key_enable_llm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("DAAS_LLM_NARRATIVE", "true")
    assert Settings(_env_file=None).llm_api_key == "sk-test-not-real"


# ---------------------------------------------------------------- brak wywolania bez flagi (end-to-end)
def test_no_http_call_with_key_in_env_but_flag_off(settings: Settings, no_http, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("DAAS_LLM_NARRATIVE", raising=False)
    reset_settings_cache()
    s = Settings(_env_file=None)
    runner = PipelineRunner(settings=s, db=DuckDBClient(s.duckdb_file))
    res = runner.run(RunRequest(pipeline="ecommerce_demo", force_mock=True, client_name="Sklep"))
    assert res.narrative_source == "TEMPLATE"
    assert "wygenerowane przez model" not in Path(res.report_path).read_text(encoding="utf-8")


def test_build_narrative_without_api_key_never_calls_http(no_http, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")  # sam klucz w srodowisku nic nie wlacza
    text, source = nar.build_narrative("ecommerce_demo", _metrics(), "Firma")
    assert source == "TEMPLATE" and "Firma" in text


# ---------------------------------------------------------------- po wlaczeniu
def test_enabled_llm_sends_minimised_payload(http_calls: list[dict]):
    metrics = _metrics()
    assert metrics.loss_orders, "fixture ma zamowienia stratne — payload nie bylby sprawdzany"
    text, source = nar.build_narrative("ecommerce_demo", metrics, "Firma Tajna", api_key="sk-test-not-real")
    assert source == "LLM" and text == "Streszczenie testowe od modelu."
    assert len(http_calls) == 1
    sent = json.dumps(http_calls[0]["json"], ensure_ascii=False)
    assert http_calls[0]["headers"]["x-api-key"] == "sk-test-not-real"
    for secret in ("order_id", "product", "Firma Tajna"):
        assert secret not in sent, secret
    for order in metrics.loss_orders:
        assert str(order["order_id"]) not in sent and str(order["product"]) not in sent
    for product in metrics.top_products:
        assert str(product["product"]) not in sent


def test_uksc_payload_has_no_host_name(http_calls: list[dict], settings: Settings):
    from app.sources.uksc_source import UkscSource
    from app.transforms.uksc_transform import transform_uksc

    src = UkscSource(settings).fetch()
    host = src.raw_meta["host"]
    metrics = transform_uksc(src.records, host, src.raw_meta["collected_at_utc"], None)
    nar.build_narrative("uksc_evidence", metrics, None, api_key="sk-test-not-real")
    sent = json.dumps(http_calls[0]["json"], ensure_ascii=False)
    assert host["name"] not in sent


def test_enabled_llm_report_is_annotated(http_calls: list[dict], settings: Settings,
                                         monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("DAAS_LLM_NARRATIVE", "true")
    s = Settings(_env_file=None, duckdb_path=settings.duckdb_path, reports_dir=settings.reports_dir)
    runner = PipelineRunner(settings=s, db=DuckDBClient(s.duckdb_file))
    for pipeline in ("ecommerce_demo", "cs2_demo", "uksc_evidence"):
        res = runner.run(RunRequest(pipeline=pipeline, force_mock=True))
        assert res.narrative_source == "LLM", pipeline
        md = Path(res.report_path).read_text(encoding="utf-8")
        html = Path(res.report_html_path).read_text(encoding="utf-8")
        assert "streszczenie wygenerowane przez model" in md, pipeline
        assert "streszczenie wygenerowane przez model" in html, pipeline
    assert len(http_calls) == 3


def test_llm_failure_falls_back_to_template(monkeypatch: pytest.MonkeyPatch):
    def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(nar.httpx, "post", boom)
    _, source = nar.build_narrative("ecommerce_demo", _metrics(), api_key="sk-test-not-real")
    assert source == "TEMPLATE"


def test_template_report_has_no_model_annotation(client: TestClient):
    run = client.post("/run", json={"pipeline": "cs2_demo"}).json()
    html = client.get(f"/reports/run/{run['run_id']}").text
    assert "wygenerowane przez model" not in html and "TEMPLATE" in html
