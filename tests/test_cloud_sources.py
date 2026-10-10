"""M-7: w trybie cloud zrodlem runu jest plik klienta albo dane syntetyczne; nigdy zasoby operatora z env."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.core.config import Settings
from tests.conftest import PROJECT_ROOT
from tests.test_jobs import hold_executor, jobs_url, upload, wait_terminal
from tests.v1_helpers import get_rows, key_headers, seed_org

CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()
PACKAGE_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "uksc" / "host_compliant.json").read_bytes()


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


@pytest.mark.parametrize("pipeline", ["ecommerce_demo", "uksc_evidence"])
def test_file_pipelines_need_an_upload_or_mock(cloud_client, cloud_settings, org, pipeline):
    hold_executor(cloud_client)
    r = cloud_client.post(jobs_url(org.org_id), json={"pipeline": pipeline}, headers=key_headers(org.api_key))
    assert r.status_code == 422 and r.json()["code"] == "upload_required"
    assert not get_rows(cloud_settings, models.Job)


def test_cs2_needs_mock_in_cloud(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    r = cloud_client.post(jobs_url(org.org_id), json={"pipeline": "cs2_demo"}, headers=key_headers(org.api_key))
    assert r.status_code == 422 and r.json()["code"] == "mock_required"
    assert not get_rows(cloud_settings, models.Job)


@pytest.mark.parametrize("pipeline", ["ecommerce_demo", "uksc_evidence", "cs2_demo"])
def test_force_mock_is_accepted_for_every_pipeline(cloud_client, cloud_settings, org, pipeline):
    hold_executor(cloud_client)
    r = cloud_client.post(jobs_url(org.org_id), json={"pipeline": pipeline, "force_mock": True},
                          headers=key_headers(org.api_key))
    assert r.status_code == 202


def test_upload_satisfies_the_requirement(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    r = cloud_client.post(jobs_url(org.org_id), headers=key_headers(org.api_key),
                          json={"pipeline": "ecommerce_demo", "upload_id": upload(cloud_client, org, "d.csv", CSV_BYTES)})
    assert r.status_code == 202
    r = cloud_client.post(jobs_url(org.org_id), headers=key_headers(org.api_key),
                          json={"pipeline": "uksc_evidence", "upload_id": upload(cloud_client, org, "p.json", PACKAGE_BYTES)})
    assert r.status_code == 202


def test_operator_environment_sources_are_never_used(cloud_factory, cloud_settings, tmp_path, monkeypatch):
    """Nawet z ustawionym ECOMMERCE_CSV_PATH job bez uploadu nie moze ruszyc: odmowa na API."""
    operator_csv = tmp_path / "operator.csv"
    operator_csv.write_bytes(CSV_BYTES)
    client, settings = cloud_factory(ECOMMERCE_CSV_PATH=str(operator_csv), APIFY_TOKEN="apify-test-token",
                                     FACEIT_API_KEY="faceit-test-key")
    seeded = seed_org(settings)
    for body in ({"pipeline": "ecommerce_demo"}, {"pipeline": "cs2_demo"}):
        r = client.post(jobs_url(seeded.org_id), json=body, headers=key_headers(seeded.api_key))
        assert r.status_code == 422, r.text
    assert not get_rows(settings, models.Job)


def test_a_queued_job_whose_upload_disappeared_fails_instead_of_reading_the_environment(cloud_factory,
                                                                                         cloud_settings):
    """Upload usuniety (np. retencja) po zakolejkowaniu: `upload_id` jest zerowane przez FK, a job nie siega po env."""
    from tests.v1_helpers import seed_job_row

    client, settings = cloud_factory()
    seeded = seed_org(settings)
    job_id = seed_job_row(settings, seeded.org_id, pipeline="ecommerce_demo", upload_id=None, force_mock=False)
    client.portal.call(lambda: client.app.state.executor.submit(job_id, seeded.org_id))
    job = wait_terminal(client, seeded, job_id)
    assert job["status"] == "failed" and job["error_code"] == "SOURCE_UNAVAILABLE"
    assert not get_rows(settings, models.RunRecord)
