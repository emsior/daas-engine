"""`GET /metrics`: token, format ekspozycji Prometheus 0.0.4, liczniki, histogram, zadania, brak danych klienta."""
from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.test_webhooks import SECRET_CLIENT_NAME, start_job
from tests.v1_helpers import API, key_headers, seed_org

TOKEN = "metrics-token-0123456789-synthetic"
BEARER = {"Authorization": f"Bearer {TOKEN}"}

SAMPLE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>(?:[^"}]|"(?:[^"\\]|\\.)*")*)\})? (?P<value>[-+]?(?:\d+(?:\.\d+)?(?:e[-+]?\d+)?|Inf|NaN))$')
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


@pytest.fixture()
def metered(cloud_factory) -> tuple[TestClient, Settings]:
    client, settings = cloud_factory(DAAS_METRICS_TOKEN=TOKEN)
    return client, settings


def parse(text: str) -> list[tuple[str, dict[str, str], float]]:
    samples = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = SAMPLE.match(line)
        assert m, f"line is not valid exposition format: {line!r}"
        labels = dict(LABEL.findall(m["labels"] or ""))
        samples.append((m["name"], labels, float(m["value"])))
    return samples


def value(samples, name: str, **labels) -> float | None:
    for n, lab, v in samples:
        if n == name and all(lab.get(k) == str(want) for k, want in labels.items()):
            return v
    return None


def scrape(client: TestClient) -> str:
    r = client.get("/metrics", headers=BEARER)
    assert r.status_code == 200, r.text
    return r.text


# ================================================================ dostep
def test_metrics_is_404_when_no_token_is_configured(cloud_client: TestClient):
    assert cloud_client.get("/metrics").status_code == 404
    assert cloud_client.get("/metrics", headers=BEARER).status_code == 404


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": f"Basic {TOKEN}"},
    {"Authorization": "Bearer "}, {"Authorization": f"Bearer {TOKEN}x"}, {"X-API-Key": TOKEN},
])
def test_metrics_rejects_a_missing_or_wrong_token(metered, headers):
    client, _ = metered
    r = client.get("/metrics", headers=headers)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert "daas_" not in r.text


def test_metrics_accepts_the_token_and_reports_prometheus_content_type(metered):
    client, _ = metered
    r = client.get("/metrics", headers=BEARER)
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert r.headers["cache-control"] == "no-store"
    assert r.text.endswith("\n")


def test_cookie_or_api_key_sessions_do_not_open_metrics(metered):
    client, settings = metered
    org = seed_org(settings)
    assert client.get("/metrics", headers=key_headers(org.api_key)).status_code == 401


# ================================================================ format
def test_every_family_has_help_and_type_before_its_samples(metered):
    client, _ = metered
    client.get(f"{API}/health")
    text = scrape(client)
    declared: dict[str, str] = {}
    helped: set[str] = set()
    for line in text.splitlines():
        if line.startswith("# HELP "):
            helped.add(line.split()[2])
        elif line.startswith("# TYPE "):
            _, _, name, kind = line.split()
            assert name in helped and kind in {"counter", "gauge", "histogram"}
            declared[name] = kind
        elif line and not line.startswith("#"):
            name = SAMPLE.match(line)["name"]
            family = re.sub(r"_(bucket|sum|count)$", "", name) if re.sub(r"_(bucket|sum|count)$", "", name) in declared else name
            assert family in declared, f"sample before TYPE: {line}"
    assert {"daas_http_requests_total", "daas_http_request_duration_seconds", "daas_jobs",
            "daas_active_tenants", "daas_build_info"} <= set(declared)


def test_request_counter_uses_route_templates_and_status(metered):
    client, settings = metered
    org = seed_org(settings)
    for _ in range(3):
        assert client.get(f"{API}/health").status_code == 200
    assert client.get(f"{API}/orgs/{org.org_id}", headers=key_headers(org.api_key)).status_code == 200
    assert client.get(f"{API}/orgs/{org.org_id}").status_code == 401
    samples = parse(scrape(client))
    assert value(samples, "daas_http_requests_total", route="/api/v1/health", status="200") == 3
    assert value(samples, "daas_http_requests_total", route="/api/v1/orgs/{org_id}", status="200") == 1
    assert value(samples, "daas_http_requests_total", route="/api/v1/orgs/{org_id}", status="401") == 1


def test_unknown_paths_collapse_into_one_label(metered):
    client, _ = metered
    for n in range(5):
        assert client.get(f"/api/v1/nope-{n}/secret-{n}").status_code == 404
    text = scrape(client)
    assert 'route="unmatched",status="404"} 5' in text
    assert "nope-" not in text and "secret-" not in text


def test_duration_histogram_is_cumulative_and_consistent(metered):
    client, _ = metered
    for _ in range(4):
        client.get(f"{API}/health")
    samples = parse(scrape(client))
    route = "/api/v1/health"
    buckets = [(lab["le"], v) for n, lab, v in samples
               if n == "daas_http_request_duration_seconds_bucket" and lab["route"] == route]
    assert buckets[-1][0] == "+Inf" and len(buckets) >= 5
    counts = [v for _, v in buckets]
    assert counts == sorted(counts)                                         # kubelki skumulowane
    assert counts[-1] == value(samples, "daas_http_request_duration_seconds_count", route=route) == 4
    total = value(samples, "daas_http_request_duration_seconds_sum", route=route)
    assert 0 < total < 30
    bounds = [float(le) for le, _ in buckets[:-1]]
    assert bounds == sorted(bounds)


def test_build_info_carries_version_and_mode_only(metered):
    client, _ = metered
    samples = parse(scrape(client))
    (info,) = [(lab, v) for n, lab, v in samples if n == "daas_build_info"]
    assert set(info[0]) == {"version", "mode"} and info[0]["mode"] == "cloud" and info[1] == 1


# ================================================================ zadania i najemcy
def test_job_gauges_cover_every_status_and_track_runs(metered):
    client, settings = metered
    org = seed_org(settings)
    before = parse(scrape(client))
    for status in ("queued", "running", "succeeded", "failed", "cancelled"):
        assert value(before, "daas_jobs", status=status) == 0
    assert value(before, "daas_active_tenants") == 0
    job = start_job(client, org)
    assert job["status"] == "succeeded"
    after = parse(scrape(client))
    assert value(after, "daas_jobs", status="succeeded") == 1
    assert value(after, "daas_active_tenants") == 1


def test_metrics_carry_no_customer_data(metered):
    client, settings = metered
    org = seed_org(settings, name="Sekretna Spolka", owner_email="tajny.czlowiek@example.test")
    client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                files={"file": ("tajny-plik-klienta.csv", b"a;b\n1;2\n", "text/csv")})
    start_job(client, org)
    client.get(f"{API}/orgs/{org.org_id}/jobs/01ABCDEFGHJKMNPQRSTVWXYZ00", headers=key_headers(org.api_key))
    text = scrape(client)
    for forbidden in (org.org_id, org.user_id, org.key_id, org.email, "tajny", "Sekretna", SECRET_CLIENT_NAME,
                      org.api_key, TOKEN, "01ABCDEFGHJKMNPQRSTVWXYZ00", "example.test"):
        assert forbidden not in text, forbidden
    labels = {k for _, lab, _ in parse(text) for k in lab}
    assert labels <= {"route", "status", "le", "version", "mode"}


def test_scrape_is_cheap_enough_to_poll(metered):
    client, _ = metered
    started = time.perf_counter()
    for _ in range(20):
        scrape(client)
    assert time.perf_counter() - started < 10
