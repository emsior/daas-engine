"""F1 (A01 S-a01-02): w selfhost REST klient dostaje skrot raportu, zeby "sprawdzic sam" jak w cloud.

`GET /reports/{pipeline}/latest` i `GET /reports/run/{run_id}` (wszystkie `fmt`) niosa naglowek
`X-Report-SHA256` = sha256 serwowanych bajtow (zgodny z `sha256sum` klienta). `POST /run` zwraca pole
`report_sha256` = sha256 raportu HTML (ten sam skrot, ktory liczy cloud przez `report_sha256`/ETag).
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


@pytest.fixture()
def run_id(client: TestClient) -> str:
    r = client.post("/run", json={"pipeline": "ecommerce_demo"})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


@pytest.mark.parametrize("fmt", ["html", "md", "json"])
@pytest.mark.parametrize("route", ["/reports/ecommerce_demo/latest", "/reports/run/{rid}"])
def test_report_route_header_equals_sha256_of_served_bytes(client: TestClient, run_id: str, fmt: str, route: str):
    r = client.get(route.format(rid=run_id), params={"fmt": fmt})
    assert r.status_code == 200, r.text
    header = r.headers.get("x-report-sha256")
    assert header is not None and len(header) == 64
    assert header == _sha(r.content)   # to, co policzy `sha256sum` na pobranym pliku


def test_latest_and_by_run_html_hashes_match(client: TestClient, run_id: str):
    latest = client.get("/reports/ecommerce_demo/latest", params={"fmt": "html"})
    by_run = client.get(f"/reports/run/{run_id}", params={"fmt": "html"})
    assert latest.status_code == by_run.status_code == 200
    assert latest.headers["x-report-sha256"] == by_run.headers["x-report-sha256"] == _sha(latest.content)


def test_post_run_returns_report_sha256_matching_html_route(client: TestClient):
    body = client.post("/run", json={"pipeline": "ecommerce_demo"}).json()
    digest = body.get("report_sha256")
    assert digest is not None and len(digest) == 64
    html = client.get(f"/reports/run/{body['run_id']}", params={"fmt": "html"})
    assert digest == html.headers["x-report-sha256"] == _sha(html.content)


def test_cs2_run_also_carries_report_sha256_and_header(client: TestClient):
    body = client.post("/run", json={"pipeline": "cs2_demo"}).json()
    assert body.get("report_sha256") and len(body["report_sha256"]) == 64
    rep = client.get("/reports/cs2_demo/latest")   # domyslny fmt=html
    assert rep.headers["x-report-sha256"] == _sha(rep.content) == body["report_sha256"]
