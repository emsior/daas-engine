"""Zadania asynchroniczne: 202 + Location, SSE, anulowanie, odzyskiwanie po restarcie, rownolegle organizacje."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control import scopes as sc
from app.control.ids import new_ulid
from app.control.tenancy import TenantPaths
from app.control.types import utcnow
from app.core.config import Settings
from tests.conftest import PROJECT_ROOT
from tests.v1_helpers import API, get_rows, key_headers, seed_key, seed_org, update_rows, with_db

CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()
PACKAGE_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "uksc" / "host_compliant.json").read_bytes()
JOB = {"pipeline": "cs2_demo", "force_mock": True}
TERMINAL = ("succeeded", "failed", "cancelled")


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def jobs_url(org_id: str) -> str:
    return f"{API}/orgs/{org_id}/jobs"


def wait_terminal(client: TestClient, org, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"{jobs_url(org.org_id)}/{job_id}", headers=key_headers(org.api_key)).json()
        if job["status"] in TERMINAL:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {job}")


def hold_executor(client: TestClient) -> list[tuple[str, str]]:
    """Zadania zostaja w `queued`: `submit` tylko zapamietuje pare (job_id, org_id)."""
    held: list[tuple[str, str]] = []
    client.app.state.executor.submit = lambda job_id, org_id: held.append((job_id, org_id))
    return held


def seed_job(settings: Settings, org_id: str, *, status: str = "queued", progress: int = 0,
             pipeline: str = "cs2_demo", message: str | None = None) -> str:
    job_id = new_ulid()

    async def go(db):
        db.add(models.Job(id=job_id, org_id=org_id, pipeline=pipeline, status=status, progress=progress,
                          message=message, force_mock=True,
                          started_at=utcnow() if status != "queued" else None))

    with_db(settings, go)
    return job_id


def upload(client: TestClient, org, name: str, data: bytes) -> str:
    r = client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                    files={"file": (name, data, "application/octet-stream")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ================================================================ 202 + Location
def test_post_job_answers_202_with_location_and_a_queued_job(cloud_client, cloud_settings, org):
    r = cloud_client.post(jobs_url(org.org_id), json=JOB, headers=key_headers(org.api_key))
    assert r.status_code == 202, r.text
    job = r.json()
    assert r.headers["location"] == f"/api/v1/orgs/{org.org_id}/jobs/{job['id']}"
    assert job["status"] == "queued" and job["progress"] == 0 and job["pipeline"] == "cs2_demo"
    assert job["run_id"] is None and job["finished_at"] is None and job["error_code"] is None
    followed = cloud_client.get(r.headers["location"], headers=key_headers(org.api_key))
    assert followed.status_code == 200 and followed.json()["id"] == job["id"]
    done = wait_terminal(cloud_client, org, job["id"])
    assert done["status"] == "succeeded" and done["progress"] == 100 and done["run_id"]
    assert done["started_at"] and done["finished_at"]


def test_a_finished_job_leaves_a_run_record_usage_and_audit(cloud_client, cloud_settings, org):
    job = wait_terminal(cloud_client, org, cloud_client.post(
        jobs_url(org.org_id), json=JOB, headers=key_headers(org.api_key)).json()["id"])
    (record,) = get_rows(cloud_settings, models.RunRecord)
    assert (record.run_id, record.org_id, record.job_id, record.pipeline) == (
        job["run_id"], org.org_id, job["id"], "cs2_demo")
    usage = cloud_client.get(f"{API}/orgs/{org.org_id}/usage", headers=key_headers(org.api_key)).json()
    assert usage["current"]["runs"] == 1
    actions = [e.action for e in get_rows(cloud_settings, models.AuditEvent, models.AuditEvent.org_id == org.org_id)]
    assert "job.created" in actions and "run.finished" in actions


def test_job_with_an_upload_runs_on_that_file(cloud_client, cloud_settings, org):
    upload_id = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    r = cloud_client.post(jobs_url(org.org_id), headers=key_headers(org.api_key),
                          json={"pipeline": "ecommerce_demo", "upload_id": upload_id})
    assert r.status_code == 202 and r.json()["upload_id"] == upload_id
    done = wait_terminal(cloud_client, org, r.json()["id"])
    assert done["status"] == "succeeded", done


def test_job_fails_with_a_code_when_the_uploaded_file_disappeared(cloud_client, cloud_settings, org):
    upload_id = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    for path in TenantPaths.for_org(cloud_settings, org.org_id).uploads.glob(f"{upload_id}.*"):
        path.unlink()
    r = cloud_client.post(jobs_url(org.org_id), headers=key_headers(org.api_key),
                          json={"pipeline": "ecommerce_demo", "upload_id": upload_id})
    done = wait_terminal(cloud_client, org, r.json()["id"])
    assert done["status"] == "failed" and done["error_code"] == "SOURCE_UNAVAILABLE"
    assert str(PROJECT_ROOT) not in json.dumps(done)  # brak sciezek w odpowiedzi


@pytest.mark.parametrize(("body", "status", "code"), [
    ({"pipeline": "nope"}, 422, "validation_error"),
    ({}, 422, "validation_error"),
    ({"pipeline": "cs2_demo", "upload_id": "z" * 32}, 422, "validation_error"),
    ({"pipeline": "cs2_demo", "upload_id": "0" * 32, "force_mock": True}, 422, "upload_not_applicable"),
    ({"pipeline": "ecommerce_demo", "upload_id": "0" * 32}, 404, "not_found"),
])
def test_job_request_validation(cloud_client, cloud_settings, org, body, status, code):
    hold_executor(cloud_client)
    r = cloud_client.post(jobs_url(org.org_id), json=body, headers=key_headers(org.api_key))
    assert r.status_code == status and r.json()["code"] == code
    assert not get_rows(cloud_settings, models.Job)


def test_upload_kind_must_match_the_pipeline(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    csv_id = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    package_id = upload(cloud_client, org, "paczka.json", PACKAGE_BYTES)
    wrong = cloud_client.post(jobs_url(org.org_id), json={"pipeline": "uksc_evidence", "upload_id": csv_id},
                              headers=key_headers(org.api_key))
    assert wrong.status_code == 422 and wrong.json()["code"] == "upload_kind_mismatch"
    wrong2 = cloud_client.post(jobs_url(org.org_id), json={"pipeline": "ecommerce_demo", "upload_id": package_id},
                               headers=key_headers(org.api_key))
    assert wrong2.status_code == 422
    ok = cloud_client.post(jobs_url(org.org_id), json={"pipeline": "uksc_evidence", "upload_id": package_id},
                           headers=key_headers(org.api_key))
    assert ok.status_code == 202


def test_an_upload_of_another_organization_cannot_be_used(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    foreign = upload(cloud_client, other, "dane.csv", CSV_BYTES)
    r = cloud_client.post(jobs_url(org.org_id), json={"pipeline": "ecommerce_demo", "upload_id": foreign},
                          headers=key_headers(org.api_key))
    assert r.status_code == 404


def test_the_queue_is_capped_per_organization(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    update_rows(cloud_settings, models.Org, {"plan": "pro"}, models.Org.id == org.org_id)  # free zatrzymuje wczesniej
    hdr = key_headers(org.api_key)
    for _ in range(20):
        assert cloud_client.post(jobs_url(org.org_id), json=JOB, headers=hdr).status_code == 202
    r = cloud_client.post(jobs_url(org.org_id), json=JOB, headers=hdr)
    assert r.status_code == 409 and r.json()["code"] == "queue_full"


def test_job_listing_filters_by_status_and_pages_with_a_cursor(cloud_client, cloud_settings, org):
    ids = [seed_job(cloud_settings, org.org_id) for _ in range(5)]
    update_rows(cloud_settings, models.Job, {"status": "failed"}, models.Job.id == ids[0])
    hdr = key_headers(org.api_key)
    queued = cloud_client.get(jobs_url(org.org_id), params={"status": "queued"}, headers=hdr).json()
    assert {j["id"] for j in queued["items"]} == set(ids[1:]) and queued["next_cursor"] is None
    first = cloud_client.get(jobs_url(org.org_id), params={"limit": 2}, headers=hdr).json()
    second = cloud_client.get(jobs_url(org.org_id), params={"limit": 2, "cursor": first["next_cursor"]},
                              headers=hdr).json()
    seen = [j["id"] for j in first["items"] + second["items"]]
    assert seen == sorted(ids, reverse=True)[:4]
    assert cloud_client.get(jobs_url(org.org_id), params={"status": "bogus"}, headers=hdr).status_code == 422


def test_job_scopes(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    _, read_only = seed_key(cloud_settings, org.org_id, scopes=[sc.RUNS_READ])
    _, write_only = seed_key(cloud_settings, org.org_id, scopes=[sc.RUNS_WRITE])
    assert cloud_client.post(jobs_url(org.org_id), json=JOB, headers=key_headers(read_only)).status_code == 403
    assert cloud_client.get(jobs_url(org.org_id), headers=key_headers(write_only)).status_code == 403
    assert cloud_client.post(jobs_url(org.org_id), json=JOB, headers=key_headers(write_only)).status_code == 202
    assert cloud_client.post(jobs_url(org.org_id), json=JOB).status_code == 401


# ================================================================ SSE
def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


def stream_events(client: TestClient, org, job_id: str) -> tuple[str, dict[str, str]]:
    with client.stream("GET", f"{jobs_url(org.org_id)}/{job_id}/events", headers=key_headers(org.api_key)) as r:
        assert r.status_code == 200, r.read()
        return "".join(r.iter_text()), dict(r.headers)


def test_sse_streams_status_progress_log_and_ends_with_done(cloud_client, cloud_settings, org):
    job_id = seed_job(cloud_settings, org.org_id)
    out: dict = {}

    def consume() -> None:
        out["body"], out["headers"] = stream_events(cloud_client, org, job_id)

    thread = threading.Thread(target=consume)
    thread.start()
    time.sleep(0.4)
    update_rows(cloud_settings, models.Job, {"status": "running", "progress": 40, "message": "mapping columns"},
                models.Job.id == job_id)
    time.sleep(1.4)
    update_rows(cloud_settings, models.Job, {"status": "succeeded", "progress": 100, "message": "done",
                                             "finished_at": utcnow(), "run_id": new_ulid()},
                models.Job.id == job_id)
    thread.join(timeout=20)
    assert not thread.is_alive(), "the stream did not close after the terminal state"
    events = parse_sse(out["body"])
    names = [n for n, _ in events]
    assert names[0] == "status" and names[-1] == "done"
    assert {"status", "progress", "log", "done"} <= set(names)
    assert out["headers"]["content-type"].startswith("text/event-stream")
    assert out["headers"]["cache-control"] == "no-store"
    statuses = [d["status"] for n, d in events if n == "status"]
    assert statuses[0] == "queued" and "running" in statuses
    assert ("progress", {"id": job_id, "progress": 40}) in events
    assert any(n == "log" and d["message"] == "mapping columns" for n, d in events)
    final = events[-1][1]
    assert final["status"] == "succeeded" and final["progress"] == 100 and final["id"] == job_id


def test_sse_of_a_real_job_ends_with_done(cloud_client, cloud_settings, org):
    job_id = cloud_client.post(jobs_url(org.org_id), json=JOB, headers=key_headers(org.api_key)).json()["id"]
    body, _ = stream_events(cloud_client, org, job_id)
    events = parse_sse(body)
    assert events[0][0] == "status" and events[-1][0] == "done"
    assert events[-1][1]["status"] == "succeeded" and events[-1][1]["run_id"]
    assert [n for n, _ in events].count("done") == 1


def test_sse_of_a_finished_job_replays_status_and_done_then_closes(cloud_client, cloud_settings, org):
    job_id = seed_job(cloud_settings, org.org_id, status="failed", progress=100, message="run failed")
    body, _ = stream_events(cloud_client, org, job_id)
    names = [n for n, _ in parse_sse(body)]
    assert names[0] == "status" and names[-1] == "done"


def test_sse_of_a_cancelled_job_closes(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    job_id = cloud_client.post(jobs_url(org.org_id), json=JOB, headers=key_headers(org.api_key)).json()["id"]
    cloud_client.post(f"{jobs_url(org.org_id)}/{job_id}/cancel", headers=key_headers(org.api_key))
    events = parse_sse(stream_events(cloud_client, org, job_id)[0])
    assert events[-1][0] == "done" and events[-1][1]["status"] == "cancelled"


def test_sse_unknown_or_foreign_job_is_404_and_needs_auth(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    foreign = seed_job(cloud_settings, other.org_id)
    hdr = key_headers(org.api_key)
    assert cloud_client.get(f"{jobs_url(org.org_id)}/{foreign}/events", headers=hdr).status_code == 404
    assert cloud_client.get(f"{jobs_url(org.org_id)}/{new_ulid()}/events", headers=hdr).status_code == 404
    assert cloud_client.get(f"{jobs_url(other.org_id)}/{foreign}/events", headers=hdr).status_code == 404
    assert cloud_client.get(f"{jobs_url(other.org_id)}/{foreign}/events").status_code == 401


# ================================================================ cancel
def test_cancel_of_a_queued_job_marks_it_cancelled_and_it_never_starts(cloud_client, cloud_settings, org):
    held = hold_executor(cloud_client)
    hdr = key_headers(org.api_key)
    job_id = cloud_client.post(jobs_url(org.org_id), json=JOB, headers=hdr).json()["id"]
    r = cloud_client.post(f"{jobs_url(org.org_id)}/{job_id}/cancel", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled" and r.json()["finished_at"] and r.json()["message"] == "cancelled"
    # zadanie, ktore juz stoi w kolejce wykonawcy, po anulowaniu nie rusza
    executor = cloud_client.app.state.executor
    del executor.submit
    cloud_client.portal.call(lambda: executor.submit(*held[0]))
    time.sleep(1.0)
    after = cloud_client.get(f"{jobs_url(org.org_id)}/{job_id}", headers=hdr).json()
    assert after["status"] == "cancelled" and after["started_at"] is None
    assert not get_rows(cloud_settings, models.RunRecord)
    assert "job.cancelled" in [e.action for e in get_rows(cloud_settings, models.AuditEvent)]


def test_cancel_twice_is_409(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    hdr = key_headers(org.api_key)
    job_id = cloud_client.post(jobs_url(org.org_id), json=JOB, headers=hdr).json()["id"]
    url = f"{jobs_url(org.org_id)}/{job_id}/cancel"
    assert cloud_client.post(url, headers=hdr).status_code == 200
    r = cloud_client.post(url, headers=hdr)
    assert r.status_code == 409 and r.json()["code"] == "not_cancellable"


@pytest.mark.parametrize("status", ["succeeded", "failed", "running"])
def test_cancel_of_a_started_or_finished_job_is_409_and_changes_nothing(cloud_client, cloud_settings, org, status):
    job_id = seed_job(cloud_settings, org.org_id, status=status, progress=50)
    r = cloud_client.post(f"{jobs_url(org.org_id)}/{job_id}/cancel", headers=key_headers(org.api_key))
    assert r.status_code == 409 and r.json()["code"] == "not_cancellable"
    (job,) = get_rows(cloud_settings, models.Job, models.Job.id == job_id)
    assert job.status == status and job.progress == 50


def test_cancel_after_a_real_run_is_409(cloud_client, cloud_settings, org):
    hdr = key_headers(org.api_key)
    job_id = cloud_client.post(jobs_url(org.org_id), json=JOB, headers=hdr).json()["id"]
    assert wait_terminal(cloud_client, org, job_id)["status"] == "succeeded"
    assert cloud_client.post(f"{jobs_url(org.org_id)}/{job_id}/cancel", headers=hdr).status_code == 409


def test_cancel_unknown_foreign_and_unauthorized(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    foreign = seed_job(cloud_settings, other.org_id)
    hdr = key_headers(org.api_key)
    assert cloud_client.post(f"{jobs_url(org.org_id)}/{new_ulid()}/cancel", headers=hdr).status_code == 404
    assert cloud_client.post(f"{jobs_url(org.org_id)}/{foreign}/cancel", headers=hdr).status_code == 404
    assert cloud_client.post(f"{jobs_url(other.org_id)}/{foreign}/cancel", headers=hdr).status_code == 404
    _, read_only = seed_key(cloud_settings, other.org_id, scopes=[sc.RUNS_READ])
    assert cloud_client.post(f"{jobs_url(other.org_id)}/{foreign}/cancel",
                             headers=key_headers(read_only)).status_code == 403
    (job,) = get_rows(cloud_settings, models.Job, models.Job.id == foreign)
    assert job.status == "queued"


# ================================================================ restart
def test_restart_marks_running_as_abandoned_and_requeues_queued_jobs(cloud_factory, cloud_settings):
    seeded = seed_org(cloud_settings)
    running = seed_job(cloud_settings, seeded.org_id, status="running", progress=45, message="mapping")
    queued = seed_job(cloud_settings, seeded.org_id)
    finished = seed_job(cloud_settings, seeded.org_id, status="succeeded", progress=100, message="done")
    client, _ = cloud_factory()          # start aplikacji = odzyskiwanie stanu z control DB (`JobExecutor.start`)
    hdr = key_headers(seeded.api_key)
    got = client.get(f"{jobs_url(seeded.org_id)}/{running}", headers=hdr).json()
    assert got["status"] == "failed" and got["error_code"] == "abandoned"
    assert got["finished_at"] and got["message"] == "interrupted by restart"
    # zadanie z kolejki wraca do wykonania i konczy sie normalnie
    assert wait_terminal(client, seeded, queued)["status"] == "succeeded"
    same = client.get(f"{jobs_url(seeded.org_id)}/{finished}", headers=hdr).json()
    assert same["status"] == "succeeded" and same["error_code"] is None
    assert [r.job_id for r in get_rows(cloud_settings, models.RunRecord)] == [queued]


def test_executor_start_is_idempotent_for_finished_and_cancelled_jobs(cloud_factory, cloud_settings):
    seeded = seed_org(cloud_settings)
    cancelled = seed_job(cloud_settings, seeded.org_id, status="cancelled")
    failed = seed_job(cloud_settings, seeded.org_id, status="failed")
    client, _ = cloud_factory()
    client.portal.call(client.app.state.executor.start)   # drugi "restart" w tej samej bazie
    for job_id, status in ((cancelled, "cancelled"), (failed, "failed")):
        job = client.get(f"{jobs_url(seeded.org_id)}/{job_id}", headers=key_headers(seeded.api_key)).json()
        assert job["status"] == status and job["error_code"] is None
    assert not get_rows(cloud_settings, models.RunRecord)


# ================================================================ rownolegle organizacje
def test_two_organizations_run_jobs_in_parallel_without_lock_errors(cloud_client, cloud_settings):
    orgs = [seed_org(cloud_settings, name=f"Org {n}", owner_email=f"o{n}@example.test") for n in range(2)]

    def start(org):
        r = cloud_client.post(jobs_url(org.org_id), json=JOB, headers=key_headers(org.api_key))
        assert r.status_code == 202, r.text
        return org, r.json()["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        started = list(pool.map(start, [orgs[n % 2] for n in range(4)]))
    results = [wait_terminal(cloud_client, org, job_id) for org, job_id in started]
    assert [r["status"] for r in results] == ["succeeded"] * 4, results
    assert all(r["error_code"] is None for r in results)
    records = get_rows(cloud_settings, models.RunRecord)
    assert len(records) == 4 and len({r.run_id for r in records}) == 4
    for org in orgs:
        assert {r.org_id for r in records if r.job_id in {j for o, j in started if o is org}} == {org.org_id}
        runs = cloud_client.get(f"{API}/orgs/{org.org_id}/runs", headers=key_headers(org.api_key)).json()
        assert len(runs["items"]) == 2
    executor = cloud_client.app.state.executor
    assert 1 <= executor.max_running_seen <= cloud_settings.daas_max_concurrent_runs
    for org in orgs:   # dane kazdej organizacji leza we wlasnym katalogu
        assert (TenantPaths.for_org(cloud_settings, org.org_id).root / "daas.duckdb").exists()


def test_jobs_of_one_organization_are_never_visible_to_another(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    mine = seed_job(cloud_settings, org.org_id)
    theirs = seed_job(cloud_settings, other.org_id)
    listing = cloud_client.get(jobs_url(org.org_id), headers=key_headers(org.api_key)).json()
    assert [j["id"] for j in listing["items"]] == [mine]
    assert cloud_client.get(f"{jobs_url(org.org_id)}/{theirs}", headers=key_headers(org.api_key)).status_code == 404
    assert cloud_client.get(f"{jobs_url(other.org_id)}/{theirs}", headers=key_headers(org.api_key)).status_code == 404
