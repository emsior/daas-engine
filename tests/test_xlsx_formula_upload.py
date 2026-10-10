"""C14: upload XLSX z formulami bez zbuforowanych wartosci zwraca ostrzezenie w `warnings` (v1 i legacy)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import app.core.xlsx_import as xi
from app.core.config import Settings
from tests.test_xlsx_upload import XLSX_MIME, fixture_workbook, post, uploads, workbook_bytes
from tests.v1_helpers import key_headers, seed_org

API = "/api/v1"
NEEDLE = "komórek z formułami bez zapisanych wartości"


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def v1_upload(client: TestClient, org, raw: bytes, **data):
    return client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                       files={"file": ("eksport.xlsx", raw, XLSX_MIME)}, data=data)


def with_formula(cell: str = "F2", sheet: str | None = None) -> bytes:
    wb = fixture_workbook()
    ws = wb[sheet] if sheet else wb.active
    ws[cell] = "=SUM(1,2)"  # openpyxl nie zapisuje wyniku: brak zbuforowanej wartosci
    return workbook_bytes(wb)


def formula_warnings(body: dict) -> list[str]:
    return [w for w in (body.get("warnings") or []) if NEEDLE in w]


def test_v1_upload_warns_about_formulas_without_values(cloud_client, cloud_settings, org):
    r = v1_upload(cloud_client, org, with_formula())
    assert r.status_code == 201, r.text
    (warning,) = formula_warnings(r.json())
    assert warning.startswith("1 ")


def test_count_covers_every_formula_cell(cloud_client, cloud_settings, org):
    wb = fixture_workbook()
    ws = wb.active
    ws["F2"], ws["F3"], ws["F4"] = "=SUM(1,2)", "=SUM(2,3)", "=SUM(3,4)"
    r = v1_upload(cloud_client, org, workbook_bytes(wb))
    assert r.status_code == 201
    (warning,) = formula_warnings(r.json())
    assert warning.startswith("3 ")


def test_no_formulas_no_formula_warning(cloud_client, cloud_settings, org):
    r = v1_upload(cloud_client, org, workbook_bytes(fixture_workbook()))
    assert r.status_code == 201 and formula_warnings(r.json()) == []


def test_formula_warning_is_appended_to_the_mapper_warnings(cloud_client, cloud_settings, org):
    clean = v1_upload(cloud_client, org, workbook_bytes(fixture_workbook())).json()["warnings"] or []
    body = v1_upload(cloud_client, org, with_formula()).json()
    assert body["warnings"][: len(clean)] == clean  # ostrzezenia mappera bez zmian, formuly na koncu
    assert NEEDLE in body["warnings"][-1]


def test_sheet_field_selects_the_scanned_sheet(cloud_client, cloud_settings, org):
    wb = fixture_workbook()
    other = wb.copy_worksheet(wb.active)
    other.title = "Inne"
    other["F2"] = "=SUM(1,2)"
    raw = workbook_bytes(wb)
    first = wb.active.title
    assert formula_warnings(v1_upload(cloud_client, org, raw, sheet=first).json()) == []
    assert len(formula_warnings(v1_upload(cloud_client, org, raw, sheet="Inne").json())) == 1


def test_stored_file_is_still_the_csv_conversion(cloud_client, cloud_settings, org):
    assert v1_upload(cloud_client, org, with_formula()).status_code == 201
    stored = next(uploads(cloud_settings).parent.glob("tenants/*/uploads/*.csv"), None) \
        or next(cloud_settings.reports_path.parent.glob("**/uploads/*.csv"))
    assert "=SUM" not in stored.read_text(encoding="utf-8")


def test_slow_formula_scan_is_bounded_and_not_fatal(cloud_client, cloud_settings, org, monkeypatch: pytest.MonkeyPatch):
    import app.sources.xlsx_formulas as xf

    monkeypatch.setattr(xi, "XLSX_PARSE_TIMEOUT_SEC", 0.2)

    def slow(path, sheet=None, cancel=None):
        for _ in range(100):
            if cancel is not None and cancel.is_set():
                return 0
            time.sleep(0.05)
        return 7

    monkeypatch.setattr(xf, "count_formulas_without_value", slow)
    started = time.monotonic()
    r = v1_upload(cloud_client, org, with_formula())
    assert time.monotonic() - started < 3
    assert r.status_code == 201 and formula_warnings(r.json()) == []  # upload przyjety, bez ostrzezenia


def test_formula_scan_runs_after_container_validation_only(cloud_client, cloud_settings, org,
                                                             monkeypatch: pytest.MonkeyPatch):
    """Plik odrzucony przez walidacje kontenera nie jest nigdy otwierany drugi raz przez skan formul."""
    import app.sources.xlsx_formulas as xf

    calls: list[object] = []
    monkeypatch.setattr(xf, "count_formulas_without_value", lambda *a, **k: calls.append(a) or 0)
    r = v1_upload(cloud_client, org, b"to nie jest archiwum")
    assert r.status_code in (415, 422) and calls == []


def test_legacy_upload_warns_too(client: TestClient):
    r = post(client, with_formula())
    assert r.status_code == 200, r.text
    assert len(formula_warnings(r.json())) == 1


def test_legacy_upload_without_formulas_has_no_formula_warning(client: TestClient):
    assert formula_warnings(post(client, workbook_bytes(fixture_workbook())).json()) == []
