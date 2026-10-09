"""POST /upload przyjmuje XLSX z limitami (wariant B): bez makr, jeden arkusz (albo `sheet`), limity wierszy,
rozpakowanych bajtow i wpisow. Plik jest konwertowany do CSV i idzie ta sama sciezka co CSV od klienta."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

import app.core.xlsx_import as xi
from app.core.config import Settings
from app.core.upload_guard import AUDIT_ALLOWED_KEYS, MAX_UPLOAD_BYTES, ReasonCode
from app.sources.csv_mapper import read_client_csv
from tests.conftest import PROJECT_ROOT

PL_FIXTURE = PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------------------------------------------------------------- budowanie plikow
def workbook_bytes(wb: openpyxl.Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def fixture_workbook() -> openpyxl.Workbook:
    """Eksport z fixtures/client_export_pl.csv jako tekstowe komorki (tak zapisuje je wiekszosc sklepow)."""
    df = read_client_csv(str(PL_FIXTURE))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Zamowienia"
    ws.append(list(df.columns))
    for row in df.itertuples(index=False):
        ws.append([str(v) for v in row])
    return wb


def rewrite_zip(raw: bytes, edit) -> bytes:
    """Kopiuje archiwum, pozwalajac `edit(name, data) -> (name, data) | None` zmienic/usunac wpis."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            res = edit(info.filename, zin.read(info.filename))
            if res is not None:
                zout.writestr(res[0], res[1])
    return out.getvalue()


def add_entry(raw: bytes, name: str, data: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info, zin.read(info.filename))
        zout.writestr(name, data)
    return out.getvalue()


def post(client: TestClient, raw: bytes, name: str = "eksport.xlsx", **data):
    return client.post("/upload", files={"file": (name, raw, XLSX_MIME)}, data=data)


def uploads(settings: Settings) -> Path:
    return settings.reports_path.parent / "uploads"


def assert_clean(settings: Settings) -> None:
    up = uploads(settings)
    if up.exists():
        assert list(up.glob("*.csv")) == [] and list(up.glob("*.tmp")) == [] and list(up.glob("*.xlsx")) == []


def audit(settings: Settings) -> list[dict]:
    p = settings.audit_log_path
    recs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []
    return [r for r in recs if r.get("action") == "upload"]


# ---------------------------------------------------------------- poprawny plik
def test_valid_xlsx_goes_through_the_csv_path(client: TestClient, settings: Settings):
    raw = workbook_bytes(fixture_workbook())
    r = post(client, raw, client_name="Sklep XLSX")
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["ready"] is True and up["rows"] == 15 and up["confidence"] >= 0.8
    assert up["filename"].endswith(".csv")  # na dysku zostaje CSV, nie skoroszyt
    assert [p.suffix for p in uploads(settings).iterdir()] == [".csv"]

    run = client.post("/run", json=up["run_payload"])
    assert run.status_code == 200 and run.json()["run_status"] == "SUCCESS"
    assert run.json()["data_status"] == "LIVE_DATA" and run.json()["client_name"] == "Sklep XLSX"


def test_xlsx_and_csv_of_the_same_data_give_the_same_metrics(client: TestClient):
    csv_up = client.post("/upload", files={"file": ("a.csv", PL_FIXTURE.read_bytes(), "text/csv")}).json()
    xlsx_up = post(client, workbook_bytes(fixture_workbook())).json()
    m_csv = client.post("/run", json=csv_up["run_payload"]).json()["metrics"]
    m_xlsx = client.post("/run", json=xlsx_up["run_payload"]).json()["metrics"]
    assert m_csv["revenue_total"] == m_xlsx["revenue_total"] and m_csv["orders_total"] == m_xlsx["orders_total"]
    assert m_csv["margin_pct"] == m_xlsx["margin_pct"]


def test_typed_cells_numbers_and_dates_are_converted(client: TestClient):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Nr zamówienia", "Data sprzedaży", "Nazwa produktu", "Kategoria", "Ilość", "Wartość brutto",
               "Koszt zakupu", "Status zamówienia"])
    ws.append(["Z-1", datetime(2026, 8, 3), "Fotel", "Meble", 2, 1299.5, 870, "Zrealizowane"])
    ws.append(["Z-2", datetime(2026, 8, 4, 13, 30), "Lampka", "Akcesoria", 1, 0.1 + 0.2, True, "Zrealizowane"])
    r = post(client, workbook_bytes(wb))
    assert r.status_code == 200, r.text
    run = client.post("/run", json=r.json()["run_payload"]).json()
    assert run["run_status"] == "SUCCESS" and run["records_processed"] == 2
    assert run["metrics"]["revenue_total"] == pytest.approx(1299.8)


def test_formulas_are_not_evaluated_or_stored_as_text(client: TestClient, settings: Settings):
    wb = fixture_workbook()
    wb.active["F2"] = "=SUM(1,2)"  # bez zbuforowanej wartosci: data_only=True daje pusta komorke
    r = post(client, workbook_bytes(wb))
    assert r.status_code == 200, r.text
    stored = next(uploads(settings).glob("*.csv")).read_text(encoding="utf-8")
    assert "=SUM" not in stored


def test_crafted_sheet_dimension_does_not_inflate_rows(client: TestClient):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Data", "Kwota", "Produkt"])
    ws.append(["2026-08-01", "100,00", "Kabel"])
    raw = rewrite_zip(workbook_bytes(wb), lambda n, d: (n, d.replace(
        b'<dimension ref="A1:C2" />', b'<dimension ref="A1:XFD1048576" />')))
    r = post(client, raw)
    assert r.status_code == 200, r.text
    assert r.json()["columns"] == ["Data", "Kwota", "Produkt"] and r.json()["rows"] == 1


# ---------------------------------------------------------------- arkusze
def two_sheet_workbook() -> openpyxl.Workbook:
    wb = fixture_workbook()
    junk = wb.create_sheet("Notatki")
    junk.append(["nic", "tu", "nie ma"])
    return wb


def test_multiple_sheets_without_selection_are_rejected(client: TestClient, settings: Settings):
    r = post(client, workbook_bytes(two_sheet_workbook()))
    assert r.status_code == 422
    assert "arkusz" in r.json()["detail"] and "Notatki" not in r.json()["detail"]
    assert audit(settings)[-1]["reason_code"] == "xlsx_multiple_sheets"
    assert_clean(settings)


def test_sheet_field_selects_one_of_many(client: TestClient):
    r = post(client, workbook_bytes(two_sheet_workbook()), sheet="Zamowienia")
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == 15


def test_unknown_sheet_is_rejected(client: TestClient, settings: Settings):
    r = post(client, workbook_bytes(two_sheet_workbook()), sheet="Nie ma takiego")
    assert r.status_code == 422
    assert audit(settings)[-1]["reason_code"] == "xlsx_sheet_not_found"
    assert_clean(settings)


def test_too_many_sheets_are_rejected_even_with_selection(client: TestClient, settings: Settings,
                                                          monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(xi, "MAX_XLSX_SHEETS", 3)
    wb = fixture_workbook()
    for i in range(3):
        wb.create_sheet(f"S{i}")
    r = post(client, workbook_bytes(wb), sheet="Zamowienia")
    assert r.status_code == 422
    assert audit(settings)[-1]["reason_code"] == "xlsx_too_many_sheets"
    assert_clean(settings)


def test_overlong_sheet_name_is_rejected_by_validation(client: TestClient):
    assert post(client, workbook_bytes(fixture_workbook()), sheet="x" * 65).status_code == 422


# ---------------------------------------------------------------- makra i rozszerzenia
@pytest.mark.parametrize("name", ["eksport.xlsm", "eksport.xls", "eksport.xltx", "eksport.xlsb", "eksport.xlsx.exe"])
def test_other_spreadsheet_extensions_are_rejected(client: TestClient, settings: Settings, name: str):
    r = post(client, workbook_bytes(fixture_workbook()), name=name)
    assert r.status_code == 415
    assert_clean(settings)


def test_uppercase_xlsx_extension_is_accepted(client: TestClient):
    assert post(client, workbook_bytes(fixture_workbook()), name="EKSPORT.XLSX").status_code == 200


def test_vba_project_inside_an_xlsx_is_rejected(client: TestClient, settings: Settings):
    raw = add_entry(workbook_bytes(fixture_workbook()), "xl/vbaProject.bin", b"\xd0\xcf\x11\xe0 fake vba")
    r = post(client, raw)
    assert r.status_code == 415
    assert audit(settings)[-1]["reason_code"] == "xlsx_macros_not_allowed"
    assert_clean(settings)


def test_macro_enabled_content_type_is_rejected(client: TestClient, settings: Settings):
    raw = rewrite_zip(workbook_bytes(fixture_workbook()), lambda n, d: (n, d.replace(
        b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        b"application/vnd.ms-excel.sheet.macroEnabled.main+xml")) if n == "[Content_Types].xml" else (n, d))
    r = post(client, raw)
    assert r.status_code == 415
    assert audit(settings)[-1]["reason_code"] == "xlsx_macros_not_allowed"
    assert_clean(settings)


@pytest.mark.parametrize("entry", ["xl/macrosheets/sheet1.xml", "xl/embeddings/oleObject1.bin"])
def test_macro_sheets_and_embedded_objects_are_rejected(client: TestClient, settings: Settings, entry: str):
    r = post(client, add_entry(workbook_bytes(fixture_workbook()), entry, b"<x/>"))
    assert r.status_code == 415
    assert_clean(settings)


# ---------------------------------------------------------------- kontener: zip-bomb, DTD, uszkodzenia
def zeros_zip(megabytes: int, extra_valid_parts: bool = True) -> bytes:
    """Poprawnie wygladajacy skoroszyt z jednym ogromnym (skompresowanym do kilku KB) wpisem."""
    base = workbook_bytes(fixture_workbook())
    out = io.BytesIO()
    chunk = b"\0" * (1024 * 1024)
    with zipfile.ZipFile(io.BytesIO(base)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        if extra_valid_parts:
            for info in zin.infolist():
                zout.writestr(info, zin.read(info.filename))
        with zout.open("xl/media/bomb.bin", "w", force_zip64=True) as fh:
            for _ in range(megabytes):
                fh.write(chunk)
    return out.getvalue()


def test_zip_bomb_is_rejected_before_parsing(client: TestClient, settings: Settings):
    raw = zeros_zip(megabytes=200)  # 200 MiB po rozpakowaniu, ~200 KB w pliku
    assert len(raw) < MAX_UPLOAD_BYTES
    r = post(client, raw)
    assert r.status_code == 413
    assert audit(settings)[-1]["reason_code"] == "xlsx_unpacked_too_large"
    assert_clean(settings)


def test_high_compression_ratio_is_rejected_below_the_absolute_limit(client: TestClient, settings: Settings,
                                                                    monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(xi, "RATIO_CHECK_FROM_BYTES", 1024 * 1024)
    raw = zeros_zip(megabytes=30)  # 30 MiB < limit bezwzgledny, ale ~1000:1
    assert 30 * 1024 * 1024 < xi.MAX_XLSX_UNCOMPRESSED_BYTES
    r = post(client, raw)
    assert r.status_code == 413
    assert audit(settings)[-1]["reason_code"] == "xlsx_unpacked_too_large"


def test_unpacked_limit_is_enforced_per_configured_value(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(xi, "MAX_XLSX_UNCOMPRESSED_BYTES", 5 * 1024)
    assert post(client, workbook_bytes(fixture_workbook())).status_code == 413


def test_too_many_zip_entries_are_rejected(client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(xi, "MAX_XLSX_ENTRIES", 5)
    r = post(client, workbook_bytes(fixture_workbook()))
    assert r.status_code == 415
    assert audit(settings)[-1]["reason_code"] == "xlsx_invalid"
    assert_clean(settings)


@pytest.mark.parametrize("part", ["docProps/core.xml", "xl/worksheets/sheet1.xml", "xl/_rels/workbook.xml.rels"])
def test_doctype_in_xml_parts_is_rejected(client: TestClient, settings: Settings, part: str):
    evil = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
            b'<root xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><t>&b;</t></root>')
    raw = rewrite_zip(workbook_bytes(fixture_workbook()), lambda n, d: (n, evil) if n == part else (n, d))
    r = post(client, raw)
    assert r.status_code == 415
    assert audit(settings)[-1]["reason_code"] == "xlsx_invalid"
    assert_clean(settings)


def test_utf16_xml_part_is_rejected(client: TestClient, settings: Settings):
    raw = rewrite_zip(workbook_bytes(fixture_workbook()),
                      lambda n, d: (n, "<?xml version='1.0'?><r/>".encode("utf-16")) if n == "docProps/app.xml" else (n, d))
    assert post(client, raw).status_code == 415
    assert_clean(settings)


@pytest.mark.parametrize("payload", [
    b"to jest zwykly tekst, nie archiwum",
    b"PK\x03\x04 uciety naglowek",
    b"",
])
def test_not_a_zip_is_rejected(client: TestClient, settings: Settings, payload: bytes):
    r = post(client, payload or b"x")
    assert r.status_code == 415
    assert_clean(settings)


def test_csv_text_with_xlsx_extension_is_rejected(client: TestClient, settings: Settings):
    r = post(client, PL_FIXTURE.read_bytes())
    assert r.status_code == 415
    assert audit(settings)[-1]["reason_code"] == "xlsx_invalid"
    assert_clean(settings)


def test_truncated_xlsx_is_rejected(client: TestClient, settings: Settings):
    raw = workbook_bytes(fixture_workbook())
    assert post(client, raw[: len(raw) // 2]).status_code == 415
    assert_clean(settings)


def test_zip_without_workbook_part_is_rejected(client: TestClient, settings: Settings):
    raw = rewrite_zip(workbook_bytes(fixture_workbook()), lambda n, d: None if n == "xl/workbook.xml" else (n, d))
    assert post(client, raw).status_code == 415
    assert_clean(settings)


def test_corrupt_sheet_xml_gives_a_generic_error_without_content(client: TestClient, settings: Settings):
    raw = rewrite_zip(workbook_bytes(fixture_workbook()),
                      lambda n, d: (n, b"<worksheet><sheetData><row><c>SEKRET-TRESC") if n == "xl/worksheets/sheet1.xml" else (n, d))
    r = post(client, raw)
    assert r.status_code in (415, 422)
    assert "SEKRET" not in r.text
    assert_clean(settings)


def test_upload_over_the_byte_limit_is_rejected_while_streaming(client: TestClient, settings: Settings):
    r = post(client, b"PK\x03\x04" + b"\0" * MAX_UPLOAD_BYTES)
    assert r.status_code == 413
    assert audit(settings)[-1]["reason_code"] == ReasonCode.FILE_TOO_LARGE
    assert_clean(settings)


# ---------------------------------------------------------------- limity wierszy i szerokosci
def numbered_workbook(rows: int, cols: int = 3) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Data", "Kwota", "Produkt"] + [f"x{i}" for i in range(cols - 3)])
    for i in range(rows):
        ws.append(["2026-08-01", 10 + i, f"P{i}"] + [1] * (cols - 3))
    return wb


def test_row_limit_constant_matches_the_contract():
    assert xi.MAX_XLSX_ROWS == 200_000


def test_rows_above_the_limit_are_rejected_and_at_the_limit_accepted(client: TestClient, settings: Settings,
                                                                   monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(xi, "MAX_XLSX_ROWS", 50)
    ok = post(client, workbook_bytes(numbered_workbook(50)))
    assert ok.status_code == 200 and ok.json()["rows"] == 50
    for p in uploads(settings).glob("*.csv"):
        p.unlink()
    r = post(client, workbook_bytes(numbered_workbook(51)))
    assert r.status_code == 413
    assert audit(settings)[-1]["reason_code"] == ReasonCode.TOO_MANY_RECORDS
    assert_clean(settings)


def test_xlsx_rows_may_exceed_the_csv_record_limit(monkeypatch: pytest.MonkeyPatch):
    from app.core.upload_guard import MAX_RECORDS, validate_csv_bytes

    body = "Data,Kwota\n" + "2026-08-01,1\n" * (MAX_RECORDS + 10)
    with pytest.raises(Exception, match="rekord"):
        validate_csv_bytes(body.encode())
    assert validate_csv_bytes(body.encode(), max_records=MAX_RECORDS + 10).record_count == MAX_RECORDS + 10


def test_too_wide_rows_are_rejected(client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(xi, "MAX_XLSX_COLUMNS", 6)
    r = post(client, workbook_bytes(numbered_workbook(3, cols=8)))
    assert r.status_code == 413
    assert audit(settings)[-1]["reason_code"] == "xlsx_too_wide"
    assert_clean(settings)


# ---------------------------------------------------------------- audyt i polityka
def test_xlsx_upload_is_audited_with_the_hash_of_the_received_file(client: TestClient, settings: Settings):
    raw = workbook_bytes(fixture_workbook())
    assert post(client, raw).status_code == 200
    ev = audit(settings)[-1]
    assert ev["decision"] == "allow" and ev["size_bytes"] == len(raw)
    assert ev["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert set(ev) <= AUDIT_ALLOWED_KEYS


def test_xlsx_is_accepted_with_policy_enforced(client: TestClient, runner):
    runner.settings.daas_policy_enforce = True
    assert post(client, workbook_bytes(fixture_workbook())).status_code == 200


def test_filename_with_traversal_is_still_rejected_for_xlsx(client: TestClient, settings: Settings):
    r = post(client, workbook_bytes(fixture_workbook()), name="../../x.xlsx")
    assert r.status_code == 400
    assert_clean(settings)
