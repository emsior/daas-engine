"""`GET /usage` (okres biezacy + poprzedni, limity planu), egzekwowanie limitow planu (429 `plan_limit`)
oraz `GET /audit` (stronicowanie kursorem, izolacja organizacji)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.control import models, service
from app.control import scopes as sc
from app.control.plans import GIB, MIB, PLAN_LIMITS, previous_period
from app.core.config import Settings
from tests.conftest import PROJECT_ROOT
from tests.v1_helpers import (
    API,
    get_rows,
    key_headers,
    seed_key,
    seed_member,
    seed_org,
    update_rows,
    user_headers,
    with_db,
)

CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def set_usage(settings: Settings, org_id: str, period: str, *, runs: int = 0, upload_bytes: int = 0) -> None:
    async def go(db):
        db.add(models.UsageCounter(org_id=org_id, period=period, runs=runs, upload_bytes=upload_bytes))

    with_db(settings, go)


def set_plan(settings: Settings, org_id: str, plan: str) -> None:
    update_rows(settings, models.Org, {"plan": plan}, models.Org.id == org_id)


# ================================================================ GET /usage
def test_usage_reports_current_and_previous_period_with_free_limits(cloud_client, cloud_settings, org):
    period = service.current_period()
    set_usage(cloud_settings, org.org_id, period, runs=3, upload_bytes=1234)
    set_usage(cloud_settings, org.org_id, previous_period(period), runs=7, upload_bytes=99)
    r = cloud_client.get(f"{API}/orgs/{org.org_id}/usage", headers=key_headers(org.api_key))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "org_id": org.org_id, "plan": "free", "retention_days": 90,
        "current": {"period": period, "runs": 3, "upload_bytes": 1234},
        "previous": {"period": previous_period(period), "runs": 7, "upload_bytes": 99},
        "limits": {"runs_per_month": 20, "upload_bytes_per_month": 200 * MIB},
    }


def test_usage_without_counters_is_zero(cloud_client, cloud_settings, org):
    body = cloud_client.get(f"{API}/orgs/{org.org_id}/usage", headers=key_headers(org.api_key)).json()
    assert (body["current"]["runs"], body["current"]["upload_bytes"]) == (0, 0)
    assert (body["previous"]["runs"], body["previous"]["upload_bytes"]) == (0, 0)


@pytest.mark.parametrize(("plan", "runs", "size"), [("free", 20, 200 * MIB), ("pro", 500, 5 * GIB),
                                                    ("selfhost", None, None)])
def test_usage_limits_follow_the_plan(cloud_client, cloud_settings, org, plan, runs, size):
    set_plan(cloud_settings, org.org_id, plan)
    body = cloud_client.get(f"{API}/orgs/{org.org_id}/usage", headers=key_headers(org.api_key)).json()
    assert body["plan"] == plan
    assert body["limits"] == {"runs_per_month": runs, "upload_bytes_per_month": size}


def test_usage_reflects_retention_days(cloud_client, cloud_settings, org):
    update_rows(cloud_settings, models.Org, {"retention_days": 30}, models.Org.id == org.org_id)
    body = cloud_client.get(f"{API}/orgs/{org.org_id}/usage", headers=key_headers(org.api_key)).json()
    assert body["retention_days"] == 30


def test_previous_period_wraps_the_year():
    assert previous_period("2026-01") == "2025-12"
    assert previous_period("2026-10") == "2026-09"


def test_usage_requires_the_usage_read_scope(cloud_client, cloud_settings, org):
    _, narrow = seed_key(cloud_settings, org.org_id, scopes=[sc.RUNS_READ])
    r = cloud_client.get(f"{API}/orgs/{org.org_id}/usage", headers=key_headers(narrow))
    assert r.status_code == 403 and r.json()["required_scope"] == sc.USAGE_READ
    _, ok = seed_key(cloud_settings, org.org_id, scopes=[sc.USAGE_READ])
    assert cloud_client.get(f"{API}/orgs/{org.org_id}/usage", headers=key_headers(ok)).status_code == 200


def test_usage_of_a_foreign_org_is_404(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    r = cloud_client.get(f"{API}/orgs/{other.org_id}/usage", headers=key_headers(org.api_key))
    assert r.status_code == 404


def test_usage_member_role_can_read(cloud_client, cloud_settings, org):
    hdr = user_headers(cloud_settings, seed_member(cloud_settings, org.org_id, "m@example.test", "member"))
    assert cloud_client.get(f"{API}/orgs/{org.org_id}/usage", headers=hdr).status_code == 200


# ================================================================ limity planu
JOB = {"pipeline": "cs2_demo", "force_mock": True}


def block_executor(cloud_client: TestClient) -> list[str]:
    """Podmienia `submit`, zeby test limitu nie uruchamial prawdziwych runow."""
    submitted: list[str] = []
    cloud_client.app.state.executor.submit = lambda job_id, org_id: submitted.append(job_id)
    return submitted


def test_job_is_refused_with_429_when_the_run_limit_is_reached(cloud_client, cloud_settings, org):
    submitted = block_executor(cloud_client)
    set_usage(cloud_settings, org.org_id, service.current_period(), runs=PLAN_LIMITS["free"].runs_per_month)
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/jobs", json=JOB, headers=key_headers(org.api_key))
    assert r.status_code == 429, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "plan_limit"
    assert (body["plan"], body["limit"], body["used"], body["resource"]) == ("free", 20, 20, "runs")
    assert not submitted and not get_rows(cloud_settings, models.Job)


def test_plan_limit_is_audited_without_customer_data(cloud_client, cloud_settings, org):
    set_usage(cloud_settings, org.org_id, service.current_period(), runs=20)
    cloud_client.post(f"{API}/orgs/{org.org_id}/jobs", json=JOB, headers=key_headers(org.api_key))
    (event,) = get_rows(cloud_settings, models.AuditEvent, models.AuditEvent.action == "auth_denied")
    assert event.org_id == org.org_id and event.actor_type == "key"
    assert event.meta == {"reason": "plan_limit", "plan": "free"}


def test_queued_jobs_count_towards_the_run_limit(cloud_client, cloud_settings, org):
    block_executor(cloud_client)
    set_usage(cloud_settings, org.org_id, service.current_period(), runs=19)
    hdr = key_headers(org.api_key)
    assert cloud_client.post(f"{API}/orgs/{org.org_id}/jobs", json=JOB, headers=hdr).status_code == 202
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/jobs", json=JOB, headers=hdr)
    assert r.status_code == 429 and r.json()["code"] == "plan_limit"


def test_job_below_the_limit_is_accepted(cloud_client, cloud_settings, org):
    block_executor(cloud_client)
    set_usage(cloud_settings, org.org_id, service.current_period(), runs=19)
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/jobs", json=JOB, headers=key_headers(org.api_key))
    assert r.status_code == 202


def test_pro_plan_has_a_higher_run_limit(cloud_client, cloud_settings, org):
    block_executor(cloud_client)
    set_plan(cloud_settings, org.org_id, "pro")
    set_usage(cloud_settings, org.org_id, service.current_period(), runs=100)
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/jobs", json=JOB, headers=key_headers(org.api_key))
    assert r.status_code == 202


def test_selfhost_plan_has_no_limit(cloud_client, cloud_settings, org):
    block_executor(cloud_client)
    set_plan(cloud_settings, org.org_id, "selfhost")
    set_usage(cloud_settings, org.org_id, service.current_period(), runs=10**6, upload_bytes=10**12)
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/jobs", json=JOB, headers=key_headers(org.api_key))
    assert r.status_code == 202
    up = cloud_client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                           files={"file": ("dane.csv", CSV_BYTES, "text/csv")})
    assert up.status_code == 201


def test_previous_month_usage_does_not_block(cloud_client, cloud_settings, org):
    block_executor(cloud_client)
    set_usage(cloud_settings, org.org_id, previous_period(service.current_period()), runs=20,
              upload_bytes=200 * MIB)
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/jobs", json=JOB, headers=key_headers(org.api_key))
    assert r.status_code == 202


def test_upload_is_refused_with_429_when_the_byte_limit_is_reached(cloud_client, cloud_settings, org):
    set_usage(cloud_settings, org.org_id, service.current_period(), upload_bytes=200 * MIB)
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                          files={"file": ("dane.csv", CSV_BYTES, "text/csv")})
    assert r.status_code == 429, r.text
    body = r.json()
    assert body["code"] == "plan_limit" and body["resource"] == "upload_bytes" and body["limit"] == 200 * MIB


def test_upload_that_would_exceed_the_limit_is_refused_by_content_length(cloud_client, cloud_settings, org):
    set_usage(cloud_settings, org.org_id, service.current_period(), upload_bytes=200 * MIB - 10)
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                          files={"file": ("dane.csv", CSV_BYTES, "text/csv")})
    assert r.status_code == 429 and r.json()["code"] == "plan_limit"


def test_upload_below_the_limit_is_accepted_and_counted(cloud_client, cloud_settings, org):
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                          files={"file": ("dane.csv", CSV_BYTES, "text/csv")})
    assert r.status_code == 201
    usage = cloud_client.get(f"{API}/orgs/{org.org_id}/usage", headers=key_headers(org.api_key)).json()
    assert usage["current"]["upload_bytes"] == len(CSV_BYTES)


def test_plan_guard_does_not_mask_auth_errors(cloud_client, cloud_settings, org):
    """Nad limitem: brak klucza nadal 401, obca organizacja nadal 404, brak zakresu nadal 403."""
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    set_usage(cloud_settings, other.org_id, service.current_period(), runs=20)
    url = f"{API}/orgs/{other.org_id}/jobs"
    assert cloud_client.post(url, json=JOB).status_code == 401
    assert cloud_client.post(url, json=JOB, headers=key_headers(org.api_key)).status_code == 404
    _, narrow = seed_key(cloud_settings, other.org_id, scopes=[sc.RUNS_READ])
    assert cloud_client.post(url, json=JOB, headers=key_headers(narrow)).status_code == 403
