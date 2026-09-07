"""Testy v0.2: mapper CSV klienta, POST /upload -> /run (LIVE_DATA), raport HTML, narracja z fallbackiem."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.models import CS2Metrics, DataStatus, EcommerceMetrics, RunRequest, RunStatus
from app.reporting.narrative import build_narrative, template_narrative
from app.sources.csv_mapper import _to_number, detect_mapping, map_dataframe, read_client_csv
from app.transforms.ecommerce_transform import transform_ecommerce

from tests.test_pipelines import PROJECT_ROOT, client, runner, settings  # noqa: F401  (fixtures)

PL_FIXTURE = PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv"


# ---------------------------------------------------------------------------
# Mapper
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("1 299,00 zł", 1299.0), ("1.299,00", 1299.0), ("1,299.00", 1299.0), ("12.5%", 12.5),
    ("-41", -41.0), ("", None), ("nan", None), (7, 7.0), ("  0,30 ", 0.3),
])
def test_to_number(raw, expected):
    assert _to_number(raw) == expected


def test_detect_mapping_polish_headers():
    cols = ["Nr zamówienia", "Data sprzedaży", "Nazwa produktu", "Kategoria", "Ilość",
            "Wartość brutto", "Koszt zakupu", "Rabat", "Status zamówienia", "Uwagi"]
    mapping, unmapped = detect_mapping(cols)
    assert mapping["order_id"] == "Nr zamówienia"
    assert mapping["date"] == "Data sprzedaży"
    assert mapping["revenue"] == "Wartość brutto"
    assert mapping["cost"] == "Koszt zakupu"
    assert mapping["status"] == "Status zamówienia"
    assert unmapped == ["Uwagi"]


def test_map_polish_export_end_to_end():
    df = read_client_csv(str(PL_FIXTURE))
    out, rep = map_dataframe(df)
    assert rep.rows_in == 15 and rep.rows_out == 15
    assert rep.confidence == 1.0
    assert "profit (= revenue - cost)" in rep.generated
    assert out.loc[0, "revenue"] == 899.0 and out.loc[1, "revenue"] == 1299.0
    assert out.loc[4, "discount"] == 0.30
    assert set(out["status"]) <= {"completed", "refunded", "cancelled", "pending"}
    assert out.loc[5, "status"] == "refunded" and out.loc[9, "status"] == "cancelled"
    assert str(out.loc[0, "date"]) == "2026-08-01"


def test_map_minimal_csv_fills_defaults():
    df = pd.DataFrame({"data": ["2026-01-05", "2026-01-06", "x"], "kwota": ["100", "250,50", "10"]})
    out, rep = map_dataframe(df)
    assert rep.rows_out == 2                      # wiersz z nieczytelną datą odpada
    assert list(out["order_id"]) == ["ROW-00001", "ROW-00002"]
    assert out["cost"].tolist() == [0.0, 0.0] and out["profit"].tolist() == [100.0, 250.5]
    assert any("koszt" in w.lower() for w in rep.warnings)
    assert rep.confidence < 1.0


def test_map_missing_revenue_raises():
    with pytest.raises(ValueError, match="przychodu"):
        map_dataframe(pd.DataFrame({"data": ["2026-01-01"], "produkt": ["x"]}))


def test_mapped_data_feeds_transform():
    out, _ = map_dataframe(read_client_csv(str(PL_FIXTURE)))
    m = transform_ecommerce(out.to_dict(orient="records"))
    assert m.orders_total == 15
    assert m.orders_refunded == 1 and m.orders_cancelled == 1
    assert m.loss_orders_count == 2
    assert 25 < m.margin_pct < 35


# ---------------------------------------------------------------------------
# Narracja
# ---------------------------------------------------------------------------
def test_narrative_falls_back_to_template(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out, _ = map_dataframe(read_client_csv(str(PL_FIXTURE)))
    m = transform_ecommerce(out.to_dict(orient="records"))
    text, source = build_narrative("ecommerce_demo", m, "Firma X")
    assert source == "TEMPLATE"
    assert "Firma X" in text and str(m.orders_total) in text and f"{m.margin_pct}%" in text


def test_narrative_llm_error_falls_back(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    import app.reporting.narrative as n

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(n.httpx, "post", boom)
    m = transform_ecommerce([])
    _, source = build_narrative("ecommerce_demo", m)
    assert source == "TEMPLATE"


def test_template_narrative_cs2_empty():
    from app.transforms.cs2_transform import transform_cs2
    assert "Brak" in template_narrative("cs2_demo", transform_cs2([]))


# ---------------------------------------------------------------------------
# Runner + raport HTML
# ---------------------------------------------------------------------------
def test_runner_with_client_file_is_live(runner):  # noqa: F811
    res = runner.run(RunRequest(pipeline="ecommerce_demo", source_path=str(PL_FIXTURE), client_name="ACME"))
    assert res.run_status == RunStatus.SUCCESS
    assert res.data_status == DataStatus.LIVE_DATA
    assert res.records_processed == 15
    assert res.client_name == "ACME" and res.narrative_source == "TEMPLATE"
    html = Path(res.report_html_path).read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and "ACME" in html and "LIVE_DATA" in html
    assert "<table>" in html and "PRAKTYCZNE WNIOSKI" in html
    assert Path(res.report_json_path).exists()
    stored = runner.db.get_run(res.run_id)
    assert stored["client_name"] == "ACME" and stored["report_html_path"] == res.report_html_path


def test_runner_missing_source_path_fails_cleanly(runner):  # noqa: F811
    res = runner.run(RunRequest(pipeline="ecommerce_demo", source_path="/does/not/exist.csv"))
    assert res.run_status == RunStatus.FAILED
    assert "not found" in (res.error or "")


# ---------------------------------------------------------------------------
# API: upload -> run -> report
# ---------------------------------------------------------------------------
def test_api_upload_run_report_flow(client: TestClient):  # noqa: F811
    files = {"file": ("export.csv", PL_FIXTURE.read_bytes(), "text/csv")}
    r = client.post("/upload", files=files, data={"client_name": "Sklep Demo"})
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["ready"] is True and up["rows"] == 15 and up["confidence"] == 1.0
    assert up["mapping"]["revenue"] == "Wartość brutto"
    assert up["run_payload"]["pipeline"] == "ecommerce_demo"

    r = client.post("/run", json=up["run_payload"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data_status"] == "LIVE_DATA" and body["records_processed"] == 15
    assert body["client_name"] == "Sklep Demo"

    r = client.get("/reports/ecommerce_demo/latest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "Sklep Demo" in r.text
    assert client.get("/reports/ecommerce_demo/latest", params={"fmt": "md"}).status_code == 200
    assert client.get("/reports/ecommerce_demo/latest", params={"fmt": "json"}).status_code == 200
    assert client.get(f"/reports/run/{body['run_id']}").status_code == 200
    assert client.get("/reports/run/nope").status_code == 404
    assert client.get("/reports/cs2_demo/latest").status_code == 404


def test_api_upload_rejects_bad_input(client: TestClient):  # noqa: F811
    assert client.post("/upload", files={"file": ("x.exe", b"abc", "application/octet-stream")}).status_code == 415
    assert client.post("/upload", files={"file": ("x.csv", b"   ", "text/csv")}).status_code == 400
    bad = client.post("/upload", files={"file": ("x.csv", b"a;b\n1;2\n", "text/csv")})
    assert bad.status_code == 422 and "przychodu" in bad.json()["detail"]
