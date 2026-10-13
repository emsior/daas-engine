"""MCP `start_run` z zakresem dat (`period_from` / `period_to`): okres w zadaniu (`start_run`, `get_job`), run liczy
tylko rekordy z okresu, walidacja jak w `POST /jobs`, `period_not_supported` oraz idempotencja liczona razem z
okresem (ten sam upload + pipeline + okres -> `reused`, inny okres -> nowe zadanie)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.core.config import Settings
from tests.conftest import PROJECT_ROOT
from tests.mcp_helpers import call_tool, error_code, ok, text_of, wait_job
from tests.v1_helpers import API, get_rows, key_headers, seed_org

#: 15 zamowien z dni 01.08.2026-14.08.2026; tydzien 01.08-07.08 to 8 zamowien.
CSV_TEXT = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_text(encoding="utf-8")
PACKAGE_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "uksc" / "host_compliant.json").read_bytes()
WEEK = {"period_from": "2026-08-01", "period_to": "2026-08-07"}


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def upload(client: TestClient, key: str) -> str:
    return ok(call_tool(client, key, "upload_csv", {"filename": "eksport.csv", "content": CSV_TEXT}))["id"]


def start(client: TestClient, key: str, upload_id: str, pipeline: str = "ecommerce_demo", **period) -> dict:
    return call_tool(client, key, "start_run", {"upload_id": upload_id, "pipeline": pipeline, **period})


def hold_executor(client: TestClient) -> None:
    client.app.state.executor.submit = lambda job_id, org_id: None  # zadania zostaja `queued`


# ================================================================ okres w runie
def test_start_run_with_a_week_counts_only_the_orders_of_that_week(cloud_client, org):
    key = org.api_key
    uid = upload(cloud_client, key)
    full = wait_job(cloud_client, key, ok(start(cloud_client, key, uid))["job_id"])
    started = start(cloud_client, key, uid, **WEEK)
    job = ok(started)
    assert (job["period_from"], job["period_to"], job["reused"]) == ("2026-08-01", "2026-08-07", False)
    assert "period 2026-08-01..2026-08-07" in text_of(started)
    done = wait_job(cloud_client, key, job["job_id"])
    assert full["status"] == done["status"] == "succeeded", (full, done)
    assert (done["period_from"], done["period_to"]) == ("2026-08-01", "2026-08-07")
    assert (full["period_from"], full["period_to"]) == (None, None)
    assert "period 2026-08-01..2026-08-07" in text_of(call_tool(cloud_client, key, "get_job",
                                                                {"job_id": job["job_id"]}))

    full_run = ok(call_tool(cloud_client, key, "get_run", {"run_id": full["run_id"]}))
    week_run = ok(call_tool(cloud_client, key, "get_run", {"run_id": done["run_id"]}))
    assert full_run["records_processed"] == 15 and week_run["records_processed"] == 8
    assert "2026-08-01" <= week_run["period_from"] <= week_run["period_to"] <= "2026-08-07"
    assert "(8 of 15 records)" in week_run["source_detail"]


def test_open_ended_period_keeps_the_missing_bound_empty(cloud_client, org):
    key = org.api_key
    uid = upload(cloud_client, key)
    started = start(cloud_client, key, uid, period_from="2026-08-10")
    job = ok(started)
    assert (job["period_from"], job["period_to"]) == ("2026-08-10", None)
    assert "period 2026-08-10.." in text_of(started)
    done = wait_job(cloud_client, key, job["job_id"])
    assert done["status"] == "succeeded" and done["period_to"] is None
    assert ok(call_tool(cloud_client, key, "get_run", {"run_id": done["run_id"]}))["records_processed"] == 5


# ================================================================ walidacja
@pytest.mark.parametrize(("period", "field"), [
    ({"period_from": "2026-08-07", "period_to": "2026-08-01"}, "period_to"),
    ({"period_from": "2025-08-15", "period_to": "2026-09-19"}, "period_to"),  # 401 dni z obydwoma koncami
    ({"period_from": "1999-12-31"}, "period_from"),
    ({"period_from": "01.08.2026"}, "period_from"),
    ({"period_to": "2026-13-01"}, "period_to"),
], ids=["reversed", "over-400-days", "before-2000", "not-iso", "no-such-month"])
def test_invalid_periods_are_validation_errors(cloud_client, cloud_settings, org, period, field):
    # nieistniejacy upload: walidacja okresu jest przed jakimkolwiek odczytem bazy (nie `not_found`)
    result = start(cloud_client, org.api_key, "f" * 32, **period)
    assert error_code(result) == "validation_error", text_of(result)
    assert field in text_of(result)
    for value in period.values():
        assert value not in text_of(result)  # wartosc klienta nie jest odbijana
    assert "Traceback" not in text_of(result) and "input_value" not in text_of(result)
    assert get_rows(cloud_settings, models.Job) == []


def test_period_for_uksc_evidence_is_period_not_supported(cloud_client, cloud_settings, org):
    up = cloud_client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                           files={"file": ("paczka.json", PACKAGE_BYTES, "application/json")})
    assert up.status_code == 201, up.text
    for period in ({"period_from": "2026-08-01"}, WEEK):
        result = start(cloud_client, org.api_key, up.json()["id"], "uksc_evidence", **period)
        assert error_code(result) == "period_not_supported", text_of(result)
        assert "uksc_evidence" in text_of(result)
    assert get_rows(cloud_settings, models.Job) == []


# ================================================================ idempotencja z okresem
def test_same_period_reuses_the_job_and_another_period_creates_a_new_one(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    key = org.api_key
    uid = upload(cloud_client, key)
    first = ok(start(cloud_client, key, uid, **WEEK))
    again = start(cloud_client, key, uid, **WEEK)
    assert ok(again) == {"job_id": first["job_id"], "status": "queued", "run_id": None, "reused": True, **WEEK}
    assert "already queued" in text_of(again) and "2026-08-01..2026-08-07" in text_of(again)

    other = ok(start(cloud_client, key, uid, period_from="2026-08-08", period_to="2026-08-14"))
    open_end = ok(start(cloud_client, key, uid, period_from="2026-08-01"))  # ta sama dolna granica, bez gornej
    whole = ok(start(cloud_client, key, uid))
    created = [other, open_end, whole]
    assert not any(j["reused"] for j in created)
    assert len({first["job_id"], *(j["job_id"] for j in created)}) == 4
    assert ok(start(cloud_client, key, uid))["job_id"] == whole["job_id"]  # bez okresu: zadanie bez okresu
    assert ok(start(cloud_client, key, uid, period_from="2026-08-01"))["job_id"] == open_end["job_id"]
    assert len(get_rows(cloud_settings, models.Job)) == 4


def test_auto_run_job_is_reused_only_without_a_period(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    schedule = {"reminder_enabled": False, "auto_run_on_upload": True}
    r = cloud_client.put(f"{API}/orgs/{org.org_id}/schedule", json=schedule, headers=key_headers(org.api_key))
    assert r.status_code == 200, r.text
    up = ok(call_tool(cloud_client, org.api_key, "upload_csv", {"filename": "eksport.csv", "content": CSV_TEXT}))
    assert up["auto_job_id"]
    # agent chce raport za tydzien: auto-run liczy caly plik, wiec to inne zadanie
    week = ok(start(cloud_client, org.api_key, up["id"], **WEEK))
    assert week["reused"] is False and week["job_id"] != up["auto_job_id"]
    same = ok(start(cloud_client, org.api_key, up["id"]))
    assert same["reused"] is True and same["job_id"] == up["auto_job_id"]
    assert (same["period_from"], same["period_to"]) == (None, None)
    jobs = {j.id: j for j in get_rows(cloud_settings, models.Job)}
    assert len(jobs) == 2 and str(jobs[week["job_id"]].period_to) == "2026-08-07"
