"""`POST /orgs/{id}/verify`: czy skrot SHA-256 nalezy do raportu tej organizacji. Odpowiedz nigdy nie niesie danych."""
from __future__ import annotations

import hashlib
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control import scopes as sc
from app.control.types import utcnow
from app.core.config import Settings
from tests.conftest import PROJECT_ROOT
from tests.v1_helpers import API, get_rows, key_headers, seed_key, seed_org, with_db

CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()
KNOWN_KEYS = {"known", "run_id", "pipeline", "created_at", "format"}
H_HTML, H_MD, H_JSON = "a1" * 32, "b2" * 32, "c3" * 32


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def verify(client: TestClient, org, sha256, **kw):
    return client.post(f"{API}/orgs/{org.org_id}/verify", json={"sha256": sha256},
                       headers=kw.pop("headers", key_headers(org.api_key)), **kw)


def seed_record(settings: Settings, org_id: str, run_id: str, *, html=H_HTML, md=H_MD, json_=H_JSON,
                pipeline: str = "ecommerce_demo", age: timedelta = timedelta()) -> None:
    async def go(db):
        db.add(models.RunRecord(run_id=run_id, org_id=org_id, pipeline=pipeline, status="SUCCESS",
                                sha256_html=html, sha256_md=md, sha256_json=json_, created_at=utcnow() - age))

    with_db(settings, go)


def only_unknown(body: dict) -> bool:
    """Nieznany skrot: `known=false` i zadnej innej informacji (brak pol albo wartosci null)."""
    return body["known"] is False and all(v is None for k, v in body.items() if k != "known")


# ================================================================ znany skrot
@pytest.mark.parametrize(("digest", "fmt"), [(H_HTML, "html"), (H_MD, "md"), (H_JSON, "json")])
def test_known_hash_returns_run_pipeline_and_creation_time(cloud_client, cloud_settings, org, digest, fmt):
    seed_record(cloud_settings, org.org_id, "11111111-1111-4111-8111-111111111111")
    r = verify(cloud_client, org, digest)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["known"] is True
    assert body["run_id"] == "11111111-1111-4111-8111-111111111111"
    assert body["pipeline"] == "ecommerce_demo"
    assert body["created_at"].startswith("20") and body["format"] == fmt
    assert set(body) <= KNOWN_KEYS


def test_hash_is_normalized_before_lookup(cloud_client, cloud_settings, org):
    seed_record(cloud_settings, org.org_id, "11111111-1111-4111-8111-111111111111")
    for variant in (H_HTML.upper(), f"  {H_HTML}\n", H_HTML.title()):
        assert verify(cloud_client, org, variant).json()["known"] is True, variant


def test_same_hash_in_two_runs_resolves_to_the_newest(cloud_client, cloud_settings, org):
    seed_record(cloud_settings, org.org_id, "00000000-0000-4000-8000-000000000001", age=timedelta(days=3))
    seed_record(cloud_settings, org.org_id, "00000000-0000-4000-8000-000000000002", age=timedelta(days=1))
    assert verify(cloud_client, org, H_MD).json()["run_id"] == "00000000-0000-4000-8000-000000000002"


# ================================================================ nieznany skrot
@pytest.mark.parametrize("digest", ["0" * 64, "f" * 64, hashlib.sha256(b"nothing").hexdigest()])
def test_unknown_hash_reveals_nothing(cloud_client, cloud_settings, org, digest):
    seed_record(cloud_settings, org.org_id, "11111111-1111-4111-8111-111111111111")
    r = verify(cloud_client, org, digest)
    assert r.status_code == 200 and only_unknown(r.json()), r.text


def test_hash_of_another_organization_is_unknown(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    seed_record(cloud_settings, other.org_id, "22222222-2222-4222-8222-222222222222", html="d4" * 32,
                md="e5" * 32, json_="f6" * 32)
    mine = verify(cloud_client, org, "d4" * 32)
    assert mine.status_code == 200 and only_unknown(mine.json())
    assert "22222222" not in mine.text and "ecommerce" not in mine.text
    theirs = verify(cloud_client, other, "d4" * 32, headers=key_headers(other.api_key))
    assert theirs.json()["known"] is True and theirs.json()["run_id"] == "22222222-2222-4222-8222-222222222222"


def test_same_hash_in_two_organizations_answers_each_with_its_own_run(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    seed_record(cloud_settings, org.org_id, "11111111-1111-4111-8111-111111111111")
    seed_record(cloud_settings, other.org_id, "22222222-2222-4222-8222-222222222222")
    assert verify(cloud_client, org, H_HTML).json()["run_id"] == "11111111-1111-4111-8111-111111111111"
    other_answer = verify(cloud_client, other, H_HTML, headers=key_headers(other.api_key))
    assert other_answer.json()["run_id"] == "22222222-2222-4222-8222-222222222222"


def test_a_null_hash_column_never_matches(cloud_client, cloud_settings, org):
    seed_record(cloud_settings, org.org_id, "11111111-1111-4111-8111-111111111111", md=None, json_=None)
    assert only_unknown(verify(cloud_client, org, "0" * 64).json())


# ================================================================ walidacja
@pytest.mark.parametrize("digest", [
    "", "a" * 63, "a" * 65, "g" * 64, "a" * 63 + "z", "sha256:" + "a" * 64, "a" * 32 + " " + "a" * 31,
    "ａ" * 64, None, 123, ["a" * 64], {"sha256": "a" * 64}, True,
])
def test_hash_format_is_validated(cloud_client, cloud_settings, org, digest):
    r = verify(cloud_client, org, digest)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "validation_error"
    assert "a" * 40 not in r.text and "ａ" not in r.text  # wartosc od klienta nie jest odbijana w bledzie


@pytest.mark.parametrize("payload", [{}, {"hash": "a" * 64}, []])
def test_missing_hash_field_is_422(cloud_client, cloud_settings, org, payload):
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/verify", json=payload, headers=key_headers(org.api_key))
    assert r.status_code == 422


def test_non_json_body_is_422(cloud_client, cloud_settings, org):
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/verify", content=b"sha256=" + b"a" * 64,
                          headers={**key_headers(org.api_key), "Content-Type": "application/json"})
    assert r.status_code == 422


# ================================================================ dostep
def test_verify_needs_authentication_scope_and_membership(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    assert cloud_client.post(f"{API}/orgs/{org.org_id}/verify", json={"sha256": H_HTML}).status_code == 401
    _, narrow = seed_key(cloud_settings, org.org_id, scopes=[sc.RUNS_READ])
    r = verify(cloud_client, org, H_HTML, headers=key_headers(narrow))
    assert r.status_code == 403 and r.json()["required_scope"] == sc.REPORTS_READ
    _, ok = seed_key(cloud_settings, org.org_id, scopes=[sc.REPORTS_READ])
    assert verify(cloud_client, org, H_HTML, headers=key_headers(ok)).status_code == 200
    assert cloud_client.post(f"{API}/orgs/{other.org_id}/verify", json={"sha256": H_HTML},
                             headers=key_headers(org.api_key)).status_code == 404


# ================================================================ z prawdziwego runu
def test_reports_of_a_real_run_verify_and_tampering_breaks_it(cloud_client, cloud_settings, org):
    hdr = key_headers(org.api_key)
    up = cloud_client.post(f"{API}/orgs/{org.org_id}/uploads", headers=hdr,
                           files={"file": ("dane.csv", CSV_BYTES, "text/csv")}).json()["id"]
    job_id = cloud_client.post(f"{API}/orgs/{org.org_id}/jobs", headers=hdr,
                               json={"pipeline": "ecommerce_demo", "upload_id": up}).json()["id"]
    for _ in range(600):
        job = cloud_client.get(f"{API}/orgs/{org.org_id}/jobs/{job_id}", headers=hdr).json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert job["status"] == "succeeded", job
    run_id = job["run_id"]
    for fmt in ("html", "md", "json"):
        report = cloud_client.get(f"{API}/orgs/{org.org_id}/runs/{run_id}/report", params={"fmt": fmt}, headers=hdr)
        assert report.status_code == 200
        local = hashlib.sha256(report.content).hexdigest()       # "sprawdz sam": skrot liczony po stronie klienta
        answer = verify(cloud_client, org, local).json()
        assert answer["known"] is True and answer["run_id"] == run_id and answer["format"] == fmt
        assert answer["pipeline"] == "ecommerce_demo"
        tampered = hashlib.sha256(report.content + b" ").hexdigest()
        assert only_unknown(verify(cloud_client, org, tampered).json())
    (record,) = get_rows(cloud_settings, models.RunRecord)
    assert record.sha256_html and record.sha256_md and record.sha256_json
