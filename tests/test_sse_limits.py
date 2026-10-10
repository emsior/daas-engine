"""SSE (M-5/C5): limity strumieni, maksymalny czas zycia, budzenie przez JobBus, zamykanie przy wylaczaniu serwera."""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control.types import utcnow
from tests.test_jobs import jobs_url, seed_job
from tests.v1_helpers import key_headers, seed_key, seed_org, update_rows


def open_stream(client: TestClient, org_id: str, job_id: str, headers: dict, results: list) -> threading.Thread:
    def run() -> None:
        started = time.monotonic()
        with client.stream("GET", f"{jobs_url(org_id)}/{job_id}/events", headers=headers) as r:
            body = "".join(r.iter_text())
            results.append((r.status_code, body, time.monotonic() - started))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def wait_open(client: TestClient, count: int, timeout: float = 5.0) -> None:
    bus = client.app.state.executor.bus
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bus.active_streams >= count:
            return
        time.sleep(0.02)
    raise AssertionError(f"only {bus.active_streams} streams opened")


def finish(client: TestClient, settings, job_id: str) -> None:
    update_rows(settings, models.Job, {"status": "succeeded", "progress": 100, "finished_at": utcnow()},
                models.Job.id == job_id)
    client.portal.call(client.app.state.executor.bus.publish, job_id)


@pytest.fixture()
def world(cloud_factory):
    def make(**env):
        client, settings = cloud_factory(**env)
        seeded = seed_org(settings)
        return client, settings, seeded

    return make


# ================================================================ limity
def test_per_principal_limit_is_5_and_slots_are_released(world):
    client, settings, org = world()
    job_id = seed_job(settings, org.org_id)
    headers = key_headers(org.api_key)
    results: list = []
    threads = [open_stream(client, org.org_id, job_id, headers, results) for _ in range(5)]
    wait_open(client, 5)
    sixth = client.get(f"{jobs_url(org.org_id)}/{job_id}/events", headers=headers)
    assert sixth.status_code == 429 and sixth.json()["code"] == "sse_limit"
    assert int(sixth.headers["retry-after"]) >= 1
    # inny podmiot (inny klucz) ma wlasny limit
    _, other_key = seed_key(settings, org.org_id)
    other: list = []
    extra = open_stream(client, org.org_id, job_id, key_headers(other_key), other)
    wait_open(client, 6)
    finish(client, settings, job_id)
    for t in (*threads, extra):
        t.join(timeout=10)
        assert not t.is_alive()
    assert [code for code, *_ in results + other] == [200] * 6
    assert client.app.state.executor.bus.active_streams == 0
    again: list = []
    t = open_stream(client, org.org_id, job_id, headers, again)         # job jest zakonczony: strumien konczy sie od razu
    t.join(timeout=10)
    assert again[0][0] == 200


def test_global_limit_applies_across_principals(world):
    client, settings, org = world(DAAS_SSE_MAX_TOTAL=3)
    job_id = seed_job(settings, org.org_id)
    keys = [key_headers(seed_key(settings, org.org_id)[1]) for _ in range(4)]
    results: list = []
    threads = [open_stream(client, org.org_id, job_id, keys[n], results) for n in range(3)]
    wait_open(client, 3)
    r = client.get(f"{jobs_url(org.org_id)}/{job_id}/events", headers=keys[3])
    assert r.status_code == 429 and r.json()["code"] == "sse_limit"
    finish(client, settings, job_id)
    for t in threads:
        t.join(timeout=10)
    assert client.app.state.executor.bus.active_streams == 0


def test_limit_check_comes_after_the_404(world):
    client, settings, org = world(DAAS_SSE_MAX_TOTAL=1)
    job_id = seed_job(settings, org.org_id)
    headers = key_headers(org.api_key)
    open_stream(client, org.org_id, job_id, headers, [])
    wait_open(client, 1)
    r = client.get(f"{jobs_url(org.org_id)}/01ARZ3NDEKTSV4RRFFQ69G5FAV/events", headers=headers)
    assert r.status_code == 404
    finish(client, settings, job_id)


def test_a_rejected_request_does_not_leak_a_slot(world):
    client, settings, org = world(DAAS_SSE_MAX_TOTAL=1)
    job_id = seed_job(settings, org.org_id)
    headers = key_headers(org.api_key)
    open_stream(client, org.org_id, job_id, headers, [])
    wait_open(client, 1)
    for _ in range(5):
        assert client.get(f"{jobs_url(org.org_id)}/{job_id}/events", headers=headers).status_code == 429
    assert client.app.state.executor.bus.active_streams == 1
    finish(client, settings, job_id)


# ================================================================ czas zycia i zamykanie
def test_stream_is_closed_after_its_maximum_lifetime(world):
    client, settings, org = world(DAAS_SSE_MAX_SECONDS=1)
    job_id = seed_job(settings, org.org_id)             # job nigdy sie nie konczy
    results: list = []
    t = open_stream(client, org.org_id, job_id, key_headers(org.api_key), results)
    t.join(timeout=8)
    assert not t.is_alive(), "stream outlived its maximum lifetime"
    status, body, elapsed = results[0]
    assert status == 200 and 0.9 <= elapsed < 4 and "event: done" not in body
    assert client.app.state.executor.bus.active_streams == 0


def test_server_shutdown_flag_ends_streams_quickly(world):
    client, settings, org = world()
    job_id = seed_job(settings, org.org_id)
    results: list = []
    threads = [open_stream(client, org.org_id, job_id, key_headers(org.api_key), results) for _ in range(3)]
    wait_open(client, 3)
    started = time.monotonic()
    client.portal.call(client.app.state.executor.bus.shutdown)
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    assert time.monotonic() - started < 5
    assert all(code == 200 and "event: done" not in body for code, body, *_ in results)
    # po zamknieciu nowe strumienie nie sa przyjmowane
    r = client.get(f"{jobs_url(org.org_id)}/{job_id}/events", headers=key_headers(org.api_key))
    assert r.status_code == 503 and r.json()["code"] == "shutting_down"


def test_executor_stop_signals_the_bus(world):
    client, settings, org = world()
    assert client.app.state.executor.bus.closing is False
    client.portal.call(client.app.state.executor.stop)
    assert client.app.state.executor.bus.closing is True


# ================================================================ budzenie bez pollingu
def test_a_published_change_wakes_the_stream_without_waiting_for_the_poll(world):
    client, settings, org = world(DAAS_SSE_HEARTBEAT_SEC=30)
    job_id = seed_job(settings, org.org_id)
    results: list = []
    t = open_stream(client, org.org_id, job_id, key_headers(org.api_key), results)
    wait_open(client, 1)
    time.sleep(0.3)
    started = time.monotonic()
    finish(client, settings, job_id)
    t.join(timeout=10)
    assert not t.is_alive()
    assert time.monotonic() - started < 2.0          # fallback poll to 5 s: bez budzenia byloby dluzej
    assert "event: done" in results[0][1]


def test_without_a_publish_the_fallback_poll_notices_the_change(world):
    client, settings, org = world(DAAS_SSE_HEARTBEAT_SEC=30)
    job_id = seed_job(settings, org.org_id)
    results: list = []
    t = open_stream(client, org.org_id, job_id, key_headers(org.api_key), results)
    wait_open(client, 1)
    update_rows(settings, models.Job, {"status": "failed", "progress": 100, "finished_at": utcnow()},
                models.Job.id == job_id)
    t.join(timeout=12)
    assert not t.is_alive() and "event: done" in results[0][1]


def test_the_app_runs_uvicorn_with_a_short_graceful_shutdown():
    import inspect

    import app.main as main

    source = inspect.getsource(main)
    assert "timeout_graceful_shutdown=5" in source
