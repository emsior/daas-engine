"""Runy w API v1: zakres okresu danych (`period_from`/`period_to`) zapisywany przy zakonczeniu runu + migracja 0002."""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.control import models
from app.control.db import alembic_config, resolve_db_url, upgrade_sync
from app.core.config import Settings
from tests.conftest import PROJECT_ROOT
from tests.test_jobs import wait_terminal
from tests.v1_helpers import API, get_rows, key_headers, seed_org, with_db

CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()
PACKAGE_PATH = PROJECT_ROOT / "data" / "fixtures" / "uksc" / "host_compliant.json"


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def csv_date_range() -> tuple[str, str]:
    rows = csv.DictReader(io.StringIO(CSV_BYTES.decode("utf-8-sig")), delimiter=";")
    days = [datetime.strptime(r["Data sprzedaży"], "%d.%m.%Y").date() for r in rows]
    return min(days).isoformat(), max(days).isoformat()


def run_pipeline(client: TestClient, org, pipeline: str, *, filename: str | None = None, data: bytes | None = None,
                 **body) -> dict:
    hdr = key_headers(org.api_key)
    payload = {"pipeline": pipeline, **body}
    if filename:
        up = client.post(f"{API}/orgs/{org.org_id}/uploads", headers=hdr,
                         files={"file": (filename, data, "application/octet-stream")})
        assert up.status_code == 201, up.text
        payload["upload_id"] = up.json()["id"]
    r = client.post(f"{API}/orgs/{org.org_id}/jobs", json=payload, headers=hdr)
    assert r.status_code == 202, r.text
    job = wait_terminal(client, org, r.json()["id"])
    assert job["status"] == "succeeded", job
    return job


def get_run(client: TestClient, org, run_id: str) -> dict:
    r = client.get(f"{API}/orgs/{org.org_id}/runs/{run_id}", headers=key_headers(org.api_key))
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================ okres danych
def test_ecommerce_run_reports_the_date_range_of_the_data(cloud_client, cloud_settings, org):
    job = run_pipeline(cloud_client, org, "ecommerce_demo", filename="dane.csv", data=CSV_BYTES)
    first, last = csv_date_range()
    assert first < last
    detail = get_run(cloud_client, org, job["run_id"])
    assert (detail["period_from"], detail["period_to"]) == (first, last)
    (record,) = get_rows(cloud_settings, models.RunRecord)
    assert (record.period_from, record.period_to) == (date.fromisoformat(first), date.fromisoformat(last))


def test_uksc_run_reports_the_collection_date_of_the_package(cloud_client, cloud_settings, org):
    collected = json.loads(PACKAGE_PATH.read_text(encoding="utf-8"))["collected_at_utc"][:10]
    job = run_pipeline(cloud_client, org, "uksc_evidence", filename="paczka.json", data=PACKAGE_PATH.read_bytes())
    detail = get_run(cloud_client, org, job["run_id"])
    assert (detail["period_from"], detail["period_to"]) == (collected, collected)


def test_cs2_run_has_no_period(cloud_client, cloud_settings, org):
    job = run_pipeline(cloud_client, org, "cs2_demo", force_mock=True)
    detail = get_run(cloud_client, org, job["run_id"])
    assert detail["period_from"] is None and detail["period_to"] is None


def test_listing_carries_the_period_too(cloud_client, cloud_settings, org):
    run_pipeline(cloud_client, org, "ecommerce_demo", filename="dane.csv", data=CSV_BYTES)
    run_pipeline(cloud_client, org, "cs2_demo", force_mock=True)
    items = cloud_client.get(f"{API}/orgs/{org.org_id}/runs", headers=key_headers(org.api_key)).json()["items"]
    by_pipeline = {i["pipeline"]: i for i in items}
    assert by_pipeline["ecommerce_demo"]["period_from"] == csv_date_range()[0]
    assert by_pipeline["cs2_demo"]["period_from"] is None and by_pipeline["cs2_demo"]["period_to"] is None
    assert all({"period_from", "period_to"} <= set(i) for i in items)


def test_period_is_per_run_not_shared(cloud_client, cloud_settings, org):
    other_csv = CSV_BYTES.replace(b"01.08.2026", b"01.07.2026")
    first = run_pipeline(cloud_client, org, "ecommerce_demo", filename="a.csv", data=CSV_BYTES)
    second = run_pipeline(cloud_client, org, "ecommerce_demo", filename="b.csv", data=other_csv)
    a, b = get_run(cloud_client, org, first["run_id"]), get_run(cloud_client, org, second["run_id"])
    assert a["period_from"] == csv_date_range()[0] and b["period_from"] == "2026-07-01"
    assert a["period_to"] == b["period_to"]


def test_period_fields_are_iso_dates_without_time(cloud_client, cloud_settings, org):
    job = run_pipeline(cloud_client, org, "ecommerce_demo", filename="dane.csv", data=CSV_BYTES)
    detail = get_run(cloud_client, org, job["run_id"])
    for key in ("period_from", "period_to"):
        assert len(detail[key]) == 10 and date.fromisoformat(detail[key])


# ================================================================ migracja 0002
def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, daas_control_db_url=f"sqlite+aiosqlite:///{tmp_path / 'c.db'}")


def _columns(settings: Settings) -> dict[str, bool]:
    async def go(db):
        rows = (await db.execute(text("PRAGMA table_info(run_records)"))).all()
        return {r[1]: bool(r[3]) for r in rows}   # nazwa -> notnull

    return with_db(settings, go)


def test_migration_0002_adds_two_nullable_date_columns_and_keeps_rows(tmp_path: Path):
    from alembic import command

    s = _settings(tmp_path)
    upgrade_sync(s, "0001")
    assert "period_from" not in _columns(s)

    async def seed(db):
        await db.execute(text("INSERT INTO orgs (id, slug, name, plan, locale, retention_days, llm_narrative, "
                              "created_at) VALUES ('O1', 'o1', 'O', 'free', 'pl', 90, 0, '2026-10-01 00:00:00')"))
        await db.execute(text("INSERT INTO run_records (run_id, org_id, pipeline, status, created_at) "
                              "VALUES ('R1', 'O1', 'cs2_demo', 'SUCCESS', '2026-10-01 00:00:00')"))

    with_db(s, seed)
    upgrade_sync(s)
    columns = _columns(s)
    assert columns["period_from"] is False and columns["period_to"] is False   # nullable
    (record,) = get_rows(s, models.RunRecord)
    assert record.run_id == "R1" and record.period_from is None and record.period_to is None
    command.downgrade(alembic_config(resolve_db_url(s)), "0001")
    assert "period_from" not in _columns(s) and "period_to" not in _columns(s)

    async def ids(db):
        return (await db.execute(text("SELECT run_id FROM run_records"))).scalars().all()

    assert with_db(s, ids) == ["R1"]
