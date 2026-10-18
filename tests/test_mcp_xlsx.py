"""MCP `upload_xlsx`: skoroszyt jako base64 idzie przez `/mcp` ta sama sciezka co `POST .../uploads` (kontrole XLSX,
arkusz `sheet`, mapper kolumn, kwota, limit planu, audyt `upload.created` z `kind="xlsx"`, auto-run). Limit
`DAAS_MCP_MAX_UPLOAD_BYTES` liczy bajty pliku PO dekodowaniu base64."""
from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control import scopes as sc
from app.control.service import current_period
from app.control.tenancy import TenantPaths
from app.core.config import Settings
from app.mcp_server import tools as mcp_tools
from tests.conftest import PROJECT_ROOT
from tests.mcp_helpers import call_tool, error_code, ok, text_of, wait_job
from tests.test_xlsx_upload import fixture_workbook, two_sheet_workbook, workbook_bytes
from tests.v1_helpers import API, get_rows, key_headers, seed_key, seed_org, with_db

#: Eksport z `client_export_pl.csv` (15 zamowien) jako skoroszyt z jednym arkuszem `Zamowienia`.
RAW = workbook_bytes(fixture_workbook())
CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def upload_xlsx(client: TestClient, key: str, raw: bytes = RAW, **extra) -> dict:
    return call_tool(client, key, "upload_xlsx", {"filename": "eksport.xlsx", "content_base64": b64(raw), **extra})


def events(settings: Settings, org_id: str, action: str) -> list[models.AuditEvent]:
    return get_rows(settings, models.AuditEvent, models.AuditEvent.org_id == org_id, models.AuditEvent.action == action)


def stored_files(settings: Settings, org_id: str) -> list[str]:
    root = TenantPaths.for_org(settings, org_id).uploads
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


# ================================================================ scenariusz
def test_upload_xlsx_then_start_run_builds_the_report(cloud_client, cloud_settings, org):
    key = org.api_key
    result = upload_xlsx(cloud_client, key)
    up = ok(result)
    assert up["kind"] == "xlsx" and up["ready"] is True and up["rows"] == 15
    assert up["suggested_pipeline"] == "ecommerce_demo" and up["mapping"]["revenue"]
    assert up["bytes"] == len(RAW) and up["sha256"] == hashlib.sha256(RAW).hexdigest()  # plik, jak przyszedl
    assert up["filename"] == "eksport.xlsx" and up["auto_job_id"] is None
    assert mcp_tools.UNTRUSTED in text_of(result) and "ready to run" in text_of(result)
    files = stored_files(cloud_settings, org.org_id)
    assert files == [f"{up['id']}.csv"]  # na dysku zostaje CSV po konwersji, nie skoroszyt
    assert ok(call_tool(cloud_client, key, "list_uploads"))["items"][0]["kind"] == "xlsx"

    job = ok(call_tool(cloud_client, key, "start_run", {"upload_id": up["id"], "pipeline": "ecommerce_demo"}))
    done = wait_job(cloud_client, key, job["job_id"])
    assert done["status"] == "succeeded", done
    run = ok(call_tool(cloud_client, key, "get_run", {"run_id": done["run_id"]}))
    assert run["status"] == "SUCCESS" and run["records_processed"] == 15
    report = ok(call_tool(cloud_client, key, "get_report", {"run_id": done["run_id"]}))
    assert report["content"].startswith("#")
    assert ok(call_tool(cloud_client, key, "get_usage"))["current"]["upload_bytes"] == len(RAW)


def test_upload_is_audited_as_xlsx_without_the_file_name(cloud_client, cloud_settings, org):
    up = ok(upload_xlsx(cloud_client, org.api_key))
    (created,) = events(cloud_settings, org.org_id, "upload.created")
    assert created.meta == {"upload_id": up["id"], "kind": "xlsx", "bytes": len(RAW),
                            "sha256": hashlib.sha256(RAW).hexdigest()}
    tool_events = [e for e in events(cloud_settings, org.org_id, "mcp.tool") if e.meta["tool"] == "upload_xlsx"]
    assert len(tool_events) == 1 and tool_events[0].meta["ok"] is True
    assert set(tool_events[0].meta) == {"tool", "ok", "duration_ms", "key_id"}
    everything = str([e.meta for e in get_rows(cloud_settings, models.AuditEvent)])
    assert "eksport.xlsx" not in everything and b64(RAW)[:40] not in everything


def test_line_wrapped_base64_is_accepted(cloud_client, org):
    wrapped = base64.encodebytes(RAW).decode("ascii")  # linie po 76 znakow, jak `base64` z wiersza polecen
    assert "\n" in wrapped
    result = call_tool(cloud_client, org.api_key, "upload_xlsx",
                       {"filename": "EKSPORT.XLSX", "content_base64": wrapped})
    assert ok(result)["rows"] == 15 and ok(result)["kind"] == "xlsx"


# ================================================================ arkusze
def test_sheet_selects_one_sheet_of_a_workbook_with_many(cloud_client, cloud_settings, org):
    raw = workbook_bytes(two_sheet_workbook())
    rejected = upload_xlsx(cloud_client, org.api_key, raw)
    assert error_code(rejected) == "upload_rejected" and "xlsx_multiple_sheets" in text_of(rejected)
    assert "Notatki" not in text_of(rejected)
    picked = ok(upload_xlsx(cloud_client, org.api_key, raw, sheet="Zamowienia"))
    assert picked["rows"] == 15 and picked["kind"] == "xlsx"
    missing = upload_xlsx(cloud_client, org.api_key, raw, sheet="Brak arkusza")
    assert error_code(missing) == "upload_rejected" and "xlsx_sheet_not_found" in text_of(missing)
    assert "Brak arkusza" not in text_of(missing)
    assert [u.id for u in get_rows(cloud_settings, models.Upload)] == [picked["id"]]


# ================================================================ walidacja wejscia
@pytest.mark.parametrize("payload", ["to nie jest base64!", "QUJDRA", "QUJD=", "zażółć", "data:;base64,QUJD"],
                         ids=["text", "no-padding", "excess-padding", "non-ascii", "data-url"])
def test_invalid_base64_is_a_validation_error_without_an_upload(cloud_client, cloud_settings, org, payload):
    result = call_tool(cloud_client, org.api_key, "upload_xlsx", {"filename": "a.xlsx", "content_base64": payload})
    assert error_code(result) == "validation_error", text_of(result)
    assert text_of(result) == "validation_error: content_base64: not valid base64"
    assert get_rows(cloud_settings, models.Upload) == []
    assert events(cloud_settings, org.org_id, "auth_denied") == []  # odrzucone przed sciezka uploadu


@pytest.mark.asyncio
async def test_excess_base64_padding_is_rejected_with_a_permissive_decoder(monkeypatch):
    call = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(daas_mcp_max_upload_bytes=3)))
    monkeypatch.setattr(mcp_tools, "call_of", lambda ctx: call)

    def permissive_decode(content, *, validate):
        assert content == "QUJD=" and validate is True
        return b"ABC"  # Older Python versions accept this excess padding even with validate=True.

    monkeypatch.setattr(mcp_tools.base64, "b64decode", permissive_decode)
    ingest = AsyncMock()
    monkeypatch.setattr(mcp_tools, "_ingest", ingest)

    with pytest.raises(mcp_tools.ToolFailure) as exc:
        await mcp_tools.upload_xlsx(None, "a.xlsx", "QUJD=")

    assert exc.value.code == "validation_error"
    assert exc.value.message == "content_base64: not valid base64"
    ingest.assert_not_awaited()


def test_whitespace_only_content_is_an_empty_file(cloud_client, cloud_settings, org):
    result = call_tool(cloud_client, org.api_key, "upload_xlsx", {"filename": "a.xlsx", "content_base64": " \n "})
    assert error_code(result) == "validation_error" and "empty" in text_of(result)
    assert get_rows(cloud_settings, models.Upload) == []


@pytest.mark.parametrize("args", [
    {"filename": "dane.csv"},
    {"filename": "dane.xlsm"},
    {"filename": "dane.xlsx.exe"},
    {"filename": "a" * 124 + ".xlsx"},
    {"filename": "dane.xlsx", "pipeline_hint": "uksc_evidence"},
    {"filename": "dane.xlsx", "pipeline_hint": "cs2_demo"},
    {"filename": "dane.xlsx", "sheet": ""},
    {"filename": "dane.xlsx", "sheet": "x" * 65},
], ids=["csv", "xlsm", "double-extension", "name-too-long", "hint-uksc", "hint-cs2", "empty-sheet", "long-sheet"])
def test_wrong_names_hints_and_sheets_are_validation_errors(cloud_client, cloud_settings, org, args):
    result = call_tool(cloud_client, org.api_key, "upload_xlsx", {"content_base64": b64(RAW), **args})
    assert error_code(result) == "validation_error", text_of(result)
    assert "input_value" not in text_of(result) and "Traceback" not in text_of(result)
    assert get_rows(cloud_settings, models.Upload) == []


def test_csv_bytes_named_xlsx_are_rejected_by_the_v1_checks(cloud_client, cloud_settings, org):
    result = upload_xlsx(cloud_client, org.api_key, CSV_BYTES)
    assert error_code(result) == "upload_rejected" and "xlsx_invalid" in text_of(result)
    denied = events(cloud_settings, org.org_id, "auth_denied")
    assert denied[-1].target == "upload" and denied[-1].meta["reason"] == "xlsx_invalid"  # ten sam audyt co v1
    assert get_rows(cloud_settings, models.Upload) == [] and stored_files(cloud_settings, org.org_id) == []


# ================================================================ limity
def test_the_limit_counts_decoded_bytes_not_base64_characters(cloud_factory):
    client, settings = cloud_factory(DAAS_MCP_MAX_UPLOAD_BYTES=len(RAW))
    seeded = seed_org(settings)
    assert len(b64(RAW)) > len(RAW)  # tekst base64 przekracza limit, plik po dekodowaniu nie
    assert ok(upload_xlsx(client, seeded.api_key))["rows"] == 15


def test_decoded_file_over_the_limit_is_file_too_large_without_a_file(cloud_factory):
    client, settings = cloud_factory(DAAS_MCP_MAX_UPLOAD_BYTES=len(RAW) - 1)
    seeded = seed_org(settings)
    result = upload_xlsx(client, seeded.api_key)
    assert error_code(result) == "upload_rejected" and "file_too_large" in text_of(result)
    assert get_rows(settings, models.Upload, models.Upload.org_id == seeded.org_id) == []
    assert stored_files(settings, seeded.org_id) == []
    assert get_rows(settings, models.UsageCounter, models.UsageCounter.org_id == seeded.org_id) == []


def test_monthly_upload_limit_of_the_plan_applies(cloud_client, cloud_settings, org):
    async def exhaust(db):
        await db.merge(models.UsageCounter(org_id=org.org_id, period=current_period(), runs=0,
                                           upload_bytes=200 * 1024 * 1024))

    with_db(cloud_settings, exhaust)
    result = upload_xlsx(cloud_client, org.api_key)
    assert error_code(result) == "plan_limit", text_of(result)
    denied = events(cloud_settings, org.org_id, "auth_denied")
    assert denied[-1].meta["reason"] == "plan_limit" and denied[-1].target == "mcp:upload_xlsx"
    assert get_rows(cloud_settings, models.Upload) == []


# ================================================================ zakresy i auto-run
def test_key_without_uploads_write_gets_insufficient_scope(cloud_client, cloud_settings, org):
    _, no_upload = seed_key(cloud_settings, org.org_id, scopes=[sc.RUNS_READ, sc.RUNS_WRITE], name="no-upload")
    result = upload_xlsx(cloud_client, no_upload)
    assert error_code(result) == "insufficient_scope"
    denied = events(cloud_settings, org.org_id, "auth_denied")
    assert denied[-1].target == "mcp:upload_xlsx" and denied[-1].meta["scope"] == sc.UPLOADS_WRITE
    assert get_rows(cloud_settings, models.Upload) == []


def test_auto_run_after_upload_xlsx_reports_the_queued_job(cloud_client, cloud_settings, org):
    cloud_client.app.state.executor.submit = lambda job_id, org_id: None  # zadanie zostaje `queued`
    schedule = {"reminder_enabled": False, "auto_run_on_upload": True}
    r = cloud_client.put(f"{API}/orgs/{org.org_id}/schedule", json=schedule, headers=key_headers(org.api_key))
    assert r.status_code == 200, r.text
    result = upload_xlsx(cloud_client, org.api_key)
    up = ok(result)
    assert up["auto_job_id"] and "do not call start_run" in text_of(result)
    (job,) = get_rows(cloud_settings, models.Job)
    assert (job.id, job.upload_id, job.pipeline) == (up["auto_job_id"], up["id"], "ecommerce_demo")
    assert job.period_from is None and job.period_to is None
