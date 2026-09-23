"""Testy v0.2: mapper CSV klienta, POST /upload -> /run (LIVE_DATA), raport HTML, narracja z fallbackiem."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.models import DataStatus, RunRequest, RunStatus
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


def test_weekly_breakdown_and_wow():
    out, _ = map_dataframe(read_client_csv(str(PL_FIXTURE)))
    m = transform_ecommerce(out.to_dict(orient="records"))
    # fixture: 01–14.08.2026 → ISO tygodnie W31 (2 dni), W32, W33
    assert [w["week"] for w in m.weekly] == ["2026-W31", "2026-W32", "2026-W33"]
    assert sum(w["orders"] for w in m.weekly) == m.orders_completed + 0 + sum(
        1 for r in out.to_dict(orient="records") if r["status"] == "pending")
    assert abs(sum(w["revenue"] for w in m.weekly) - m.revenue_total) < 0.01
    assert m.wow and m.wow["week"] == "2026-W33" and m.wow["prev_week"] == "2026-W32"
    assert m.wow["revenue_delta_pct"] is not None and m.wow["orders_delta"] == 0
    assert m.wow["margin_delta_pp"] == round(m.weekly[-1]["margin_pct"] - m.weekly[-2]["margin_pct"], 1)


def test_single_week_has_no_wow():
    rows = [{"order_id": f"A{i}", "date": f"2026-08-0{i}", "product": "x", "category": "c", "quantity": 1,
             "revenue": 100.0, "cost": 60.0, "profit": 40.0, "discount": 0.0, "status": "completed"} for i in range(3, 8)]
    m = transform_ecommerce(rows)
    assert len(m.weekly) == 1 and m.wow is None


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
    # Security fix: source_path must be inside uploads/
    uploads = runner.settings.reports_path.parent / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    dest = uploads / "test_client_export.csv"
    dest.write_bytes(PL_FIXTURE.read_bytes())
    res = runner.run(RunRequest(pipeline="ecommerce_demo", source_path=str(dest), client_name="ACME"))
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
    assert "outside" in (res.error or "") or "not found" in (res.error or "")


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
    assert bad.status_code == 422
    # PR #3: komunikat odmowy jest ogólny i nie zawiera nagłówków z pliku klienta.
    # Wcześniej wypisywał listę kolumn, co przenosiło treść pliku do odpowiedzi HTTP.
    detail = bad.json()["detail"]
    assert "kolumn" in detail
    assert "a;b" not in detail and "'a'" not in detail


def test_api_upload_rejects_xlsx(client: TestClient, settings):  # noqa: F811
    """XLSX nie jest już przyjmowany przez /upload — wyłącznie CSV (PR #3, punkt 2 kontraktu).

    Zastępuje wcześniejszy test_api_upload_xlsx_export, który sprawdzał, że upload
    pliku .xlsx kończy się powodzeniem. Kontrakt PR #3 zawęża wejście do .csv, bo
    parser XLSX to nieporównanie szersza powierzchnia ataku niż tekstowy CSV.

    Skutek dla klienta: eksport z Allegro/Shoper/Excela trzeba zapisać jako CSV.
    """

    import openpyxl

    df = read_client_csv(str(PL_FIXTURE))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(df.columns))
    for row in df.itertuples(index=False):
        ws.append([str(v) for v in row])
    buf = io.BytesIO()
    wb.save(buf)

    r = client.post("/upload", files={"file": ("export_allegro.xlsx", buf.getvalue(),
                                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    data={"client_name": "Sklep XLSX"})

    assert r.status_code == 415, r.text

    # Odmowa nie może zostawić po sobie ani pliku finalnego, ani tymczasowego.
    uploads = settings.reports_path.parent / "uploads"
    if uploads.exists():
        assert list(uploads.glob("*.csv")) == []
        assert list(uploads.glob("*.tmp")) == []
