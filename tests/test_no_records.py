"""C9: run PARTIAL bez rekordow nie zuzywa limitu planu, a job konczy sie `failed` z `error_code=no_records`."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.control import models, service
from app.core.config import Settings
from app.core.models import DataStatus
from app.sources.cs2_source import SourceResult
from app.sources.ecommerce_source import EcommerceSource
from tests.conftest import PROJECT_ROOT
from tests.test_jobs import jobs_url, upload, wait_terminal
from tests.v1_helpers import get_rows, key_headers, seed_org

CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()
API = "/api/v1"


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def runs_used(settings: Settings, org_id: str) -> int:
    rows = get_rows(settings, models.UsageCounter, models.UsageCounter.org_id == org_id,
                    models.UsageCounter.period == service.current_period())
    return rows[0].runs if rows else 0


def start(client: TestClient, org, **body) -> dict:
    r = client.post(jobs_url(org.org_id), json={"pipeline": "ecommerce_demo", **body}, headers=key_headers(org.api_key))
    assert r.status_code == 202, r.text
    return wait_terminal(client, org, r.json()["id"])


def test_empty_source_fails_the_job_and_does_not_consume_the_limit(cloud_client, cloud_settings, org,
                                                                   monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(EcommerceSource, "fetch",
                        lambda self, force_mock=False, source_path=None: SourceResult([], DataStatus.LIVE_DATA, "empty"))
    uid = upload(cloud_client, org, "d.csv", CSV_BYTES)
    job = start(cloud_client, org, upload_id=uid)

    assert job["status"] == "failed" and job["error_code"] == "no_records"
    assert "no records" in job["message"]
    assert runs_used(cloud_settings, org.org_id) == 0
    # run zostaje w historii (z raportem i hashami) jako PARTIAL, ale nie liczy sie do planu
    (rec,) = get_rows(cloud_settings, models.RunRecord, models.RunRecord.org_id == org.org_id)
    assert rec.status == "PARTIAL" and rec.run_id == job["run_id"] and rec.job_id == job["id"]
    usage = cloud_client.get(f"{API}/orgs/{org.org_id}/usage", headers=key_headers(org.api_key)).json()
    assert usage["current"]["runs"] == 0


def test_repeated_empty_runs_never_reach_the_plan_limit(cloud_client, cloud_settings, org,
                                                        monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(EcommerceSource, "fetch",
                        lambda self, force_mock=False, source_path=None: SourceResult([], DataStatus.LIVE_DATA, "empty"))
    uid = upload(cloud_client, org, "d.csv", CSV_BYTES)
    for _ in range(3):
        assert start(cloud_client, org, upload_id=uid)["error_code"] == "no_records"
    assert runs_used(cloud_settings, org.org_id) == 0


def test_run_finished_audit_and_webhook_event_report_failed(cloud_client, cloud_settings, org,
                                                            monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(EcommerceSource, "fetch",
                        lambda self, force_mock=False, source_path=None: SourceResult([], DataStatus.LIVE_DATA, "empty"))
    events: list = []

    async def listener(event):
        events.append(event)

    cloud_client.app.state.executor.listeners.append(listener)
    job = start(cloud_client, org, upload_id=upload(cloud_client, org, "d.csv", CSV_BYTES))
    deadline_events = [e for e in events if e.job_id == job["id"]]
    assert deadline_events and deadline_events[0].status == "failed" and deadline_events[0].run_status == "PARTIAL"
    audit = get_rows(cloud_settings, models.AuditEvent, models.AuditEvent.action == "run.finished")
    assert audit and audit[-1].meta["status"] == "PARTIAL"


def test_run_with_records_still_succeeds_and_consumes_one(cloud_client, cloud_settings, org):
    job = start(cloud_client, org, upload_id=upload(cloud_client, org, "d.csv", CSV_BYTES))
    assert job["status"] == "succeeded" and job["error_code"] is None
    assert runs_used(cloud_settings, org.org_id) == 1


def test_force_mock_run_with_records_consumes_one(cloud_client, cloud_settings, org):
    job = start(cloud_client, org, force_mock=True)
    assert job["status"] == "succeeded"
    assert runs_used(cloud_settings, org.org_id) == 1
