"""Zakres dat runu (`period_from` / `period_to`) w `POST /jobs`: walidacja, zapis w zadaniu, `JobOut`, audyt
`job.created` oraz filtr rekordow `ecommerce_demo` w runnerze PRZED zapisem i metrykami.

Fixture `client_export_pl.csv`: 15 zamowien z dni 01.08.2026-14.08.2026 (dwa 01.08, potem po jednym dziennie);
tydzien 01.08-07.08 to 8 zamowien."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.control import models, service
from app.core.config import Settings
from app.core.models import RunRequest, RunStatus
from tests.conftest import PROJECT_ROOT
from tests.test_jobs import hold_executor, jobs_url, upload, wait_terminal
from tests.test_xlsx_upload import fixture_workbook, workbook_bytes
from tests.v1_helpers import API, get_rows, key_headers, seed_org

CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()
PACKAGE_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "uksc" / "host_compliant.json").read_bytes()
WEEK = {"period_from": "2026-08-01", "period_to": "2026-08-07"}


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def runs_used(settings: Settings, org_id: str) -> int:
    rows = get_rows(settings, models.UsageCounter, models.UsageCounter.org_id == org_id,
                    models.UsageCounter.period == service.current_period())
    return rows[0].runs if rows else 0


def post_job(client: TestClient, org, **body):
    return client.post(jobs_url(org.org_id), json={"pipeline": "ecommerce_demo", **body},
                       headers=key_headers(org.api_key))


def finished(client: TestClient, org, **body) -> dict:
    r = post_job(client, org, **body)
    assert r.status_code == 202, r.text
    return wait_terminal(client, org, r.json()["id"])


def run_detail(client: TestClient, org, run_id: str) -> dict:
    r = client.get(f"{API}/orgs/{org.org_id}/runs/{run_id}", headers=key_headers(org.api_key))
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================ filtr w runie
def test_week_period_counts_only_the_orders_of_that_week(cloud_client, cloud_settings, org):
    uid = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    full = finished(cloud_client, org, upload_id=uid)
    r = post_job(cloud_client, org, upload_id=uid, **WEEK)
    assert r.status_code == 202, r.text
    assert (r.json()["period_from"], r.json()["period_to"]) == ("2026-08-01", "2026-08-07")
    week = wait_terminal(cloud_client, org, r.json()["id"])
    assert full["status"] == week["status"] == "succeeded", (full, week)
    assert (full["period_from"], full["period_to"]) == (None, None)
    assert (week["period_from"], week["period_to"]) == ("2026-08-01", "2026-08-07")

    all_rows, week_rows = run_detail(cloud_client, org, full["run_id"]), run_detail(cloud_client, org, week["run_id"])
    assert all_rows["records_processed"] == 15 and week_rows["records_processed"] == 8
    assert week_rows["metrics"]["orders_total"] == 8
    assert week_rows["metrics"]["revenue_total"] < all_rows["metrics"]["revenue_total"]
    assert "period 2026-08-01..2026-08-07 (8 of 15 records)" in week_rows["source_detail"]
    assert "period" not in all_rows["source_detail"]
    # zakres runu (faktyczne dane) miesci sie w zamowionym okresie
    assert (week_rows["period_from"], week_rows["period_to"]) == ("2026-08-01", "2026-08-07")
    assert (all_rows["period_from"], all_rows["period_to"]) == ("2026-08-01", "2026-08-14")
    (record,) = get_rows(cloud_settings, models.RunRecord, models.RunRecord.run_id == week["run_id"])
    assert date(2026, 8, 1) <= record.period_from <= record.period_to <= date(2026, 8, 7)
    report = cloud_client.get(f"{API}/orgs/{org.org_id}/runs/{week['run_id']}/report", params={"fmt": "md"},
                              headers=key_headers(org.api_key))
    assert report.status_code == 200 and "8 of 15 records" in report.text
    assert runs_used(cloud_settings, org.org_id) == 2


# Scenariusz z testu obcego a02 (S-a02-01, BLOKER B1): kolumna daty miesza ISO, natywna date
# Excela (`...00:00:00`) i `DD.MM.YYYY`, wszystkie w tygodniu 2026-08-01..07, dni <= 12.
# Dawniej `format="mixed", dayfirst=True` czytal ISO/natywne jako d<->m (05 -> maj, 03 -> marzec),
# rozsypujac tydzien po miesiacach i ZANIZAJAC raport tygodniowy. Teraz: per komorka + ostrzezenie.
A02_MIXED_CSV = (
    b"order_id,date,revenue\n"
    b"A,2026-08-05,100\n"
    b"B,03.08.2026,200\n"
    b"C,2026-08-03 00:00:00,300\n"
    b"D,06.08.2026,400\n"
    b"E,2026-08-07,500\n"
)


def test_mixed_date_formats_do_not_understate_the_weekly_report(cloud_client, org):
    """B1 end-to-end: wszystkie 5 zamowien zostaje w zamowionym tygodniu, raport nie jest zanizony."""
    uid = upload(cloud_client, org, "mieszane_daty.csv", A02_MIXED_CSV)
    week = finished(cloud_client, org, upload_id=uid, **WEEK)
    assert week["status"] == "succeeded", week
    detail = run_detail(cloud_client, org, week["run_id"])
    # per-komorka: wszystkie 5 trafia do 2026-08-01..07 (stary parser zostawilby tylko 2 z DD.MM)
    assert detail["records_processed"] == 5
    assert detail["metrics"]["orders_total"] == 5
    assert detail["metrics"]["revenue_total"] == 1500.0
    assert "(5 of 5 records)" in detail["source_detail"]
    # glosne ostrzezenie o mieszanych formatach dociera do klienta ta sama sciezka co inne warningi
    assert "mieszane formaty" in detail["source_detail"]


@pytest.mark.parametrize(("period", "expected"), [
    ({"period_from": "2026-08-10"}, 5),
    ({"period_to": "2026-08-02"}, 3),
    ({"period_from": "2026-08-14", "period_to": "2026-08-14"}, 1),
], ids=["from-only", "to-only", "single-day"])
def test_open_ended_and_single_day_periods(cloud_client, org, period, expected):
    uid = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    job = finished(cloud_client, org, upload_id=uid, **period)
    assert job["status"] == "succeeded", job
    assert run_detail(cloud_client, org, job["run_id"])["records_processed"] == expected


def test_xlsx_upload_is_filtered_by_the_period_too(cloud_client, org):
    uid = upload(cloud_client, org, "eksport.xlsx", workbook_bytes(fixture_workbook()))
    job = finished(cloud_client, org, upload_id=uid, **WEEK)
    assert job["status"] == "succeeded", job
    assert run_detail(cloud_client, org, job["run_id"])["records_processed"] == 8


def test_period_without_data_fails_with_no_records_and_uses_no_run(cloud_client, cloud_settings, org):
    uid = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    job = finished(cloud_client, org, upload_id=uid, period_from="2026-09-01", period_to="2026-09-07")
    assert job["status"] == "failed" and job["error_code"] == "no_records", job
    assert runs_used(cloud_settings, org.org_id) == 0
    detail = run_detail(cloud_client, org, job["run_id"])
    assert detail["records_processed"] == 0 and "(0 of 15 records)" in detail["source_detail"]


# ================================================================ walidacja
@pytest.mark.parametrize(("period", "field"), [
    ({"period_from": "2026-08-07", "period_to": "2026-08-01"}, "period_to"),
    ({"period_from": "2025-08-15", "period_to": "2026-09-19"}, "period_to"),  # 401 dni z obydwoma koncami
    ({"period_from": "1999-12-31"}, "period_from"),
    ({"period_to": "1999-06-01"}, "period_to"),
    ({"period_from": "01.08.2026"}, "period_from"),
    ({"period_from": "2026-02-30"}, "period_from"),
], ids=["reversed", "over-400-days", "from-1999", "to-1999", "not-iso", "no-such-day"])
def test_invalid_periods_are_422_naming_the_field(cloud_client, cloud_settings, org, period, field):
    hold_executor(cloud_client)
    r = post_job(cloud_client, org, upload_id="0" * 32, **period)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "validation_error"
    assert any(e["loc"][-1] == field for e in body["errors"]), body
    for value in period.values():
        assert value not in r.text  # wartosc klienta nie jest odbijana
    assert get_rows(cloud_settings, models.Job) == []


def test_a_400_day_period_is_the_longest_accepted(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    uid = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    start = date(2025, 8, 15)
    last = start + timedelta(days=399)  # 400 dni z obydwoma koncami
    ok = post_job(cloud_client, org, upload_id=uid, period_from=start.isoformat(), period_to=last.isoformat())
    assert ok.status_code == 202, ok.text
    over = post_job(cloud_client, org, upload_id=uid, period_from=start.isoformat(),
                    period_to=(last + timedelta(days=1)).isoformat())
    assert over.status_code == 422 and over.json()["code"] == "validation_error"
    assert len(get_rows(cloud_settings, models.Job)) == 1


def test_period_is_not_supported_for_pipelines_without_an_order_export(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    package = upload(cloud_client, org, "paczka.json", PACKAGE_BYTES)
    for body in ({"pipeline": "cs2_demo", "force_mock": True, **WEEK},
                 {"pipeline": "uksc_evidence", "upload_id": package, "period_from": "2026-08-01"},
                 {"pipeline": "uksc_evidence", "upload_id": package, "period_to": "2026-08-01"}):
        r = cloud_client.post(jobs_url(org.org_id), json=body, headers=key_headers(org.api_key))
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "period_not_supported" and body["pipeline"] in r.json()["detail"]
    assert get_rows(cloud_settings, models.Job) == []
    # bez okresu te same pipeline'y dzialaja jak dotad
    assert cloud_client.post(jobs_url(org.org_id), json={"pipeline": "cs2_demo", "force_mock": True},
                             headers=key_headers(org.api_key)).status_code == 202


# ================================================================ JobOut, lista, audyt
def test_job_answers_and_listing_carry_the_requested_period(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    uid = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    with_period = post_job(cloud_client, org, upload_id=uid, **WEEK).json()
    without = post_job(cloud_client, org, upload_id=uid).json()
    assert (without["period_from"], without["period_to"]) == (None, None)
    hdr = key_headers(org.api_key)
    got = cloud_client.get(f"{jobs_url(org.org_id)}/{with_period['id']}", headers=hdr).json()
    assert (got["period_from"], got["period_to"]) == ("2026-08-01", "2026-08-07")
    listing = {j["id"]: j for j in cloud_client.get(jobs_url(org.org_id), headers=hdr).json()["items"]}
    assert (listing[with_period["id"]]["period_from"], listing[with_period["id"]]["period_to"]) == (
        "2026-08-01", "2026-08-07")
    assert listing[without["id"]]["period_from"] is None and listing[without["id"]]["period_to"] is None
    (job,) = get_rows(cloud_settings, models.Job, models.Job.id == with_period["id"])
    assert (job.period_from, job.period_to) == (date(2026, 8, 1), date(2026, 8, 7))


def test_job_created_audit_carries_the_period_only_when_set(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    uid = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    first = post_job(cloud_client, org, upload_id=uid, period_from="2026-08-03").json()["id"]
    second = post_job(cloud_client, org, upload_id=uid).json()["id"]
    events = {e.meta["job_id"]: e.meta for e in get_rows(cloud_settings, models.AuditEvent,
                                                         models.AuditEvent.action == "job.created")}
    assert events[first]["period_from"] == "2026-08-03" and "period_to" not in events[first]
    assert "period_from" not in events[second] and "period_to" not in events[second]


# ================================================================ runner (bez API)
def test_runner_filters_the_demo_fixture_inclusively(runner):
    full = runner.run(RunRequest(pipeline="ecommerce_demo", force_mock=True))
    week = runner.run(RunRequest(pipeline="ecommerce_demo", force_mock=True,
                                 period_from=date(2026, 8, 1), period_to=date(2026, 8, 7)))
    assert full.run_status == week.run_status == RunStatus.SUCCESS
    assert full.records_processed == 20 and week.records_processed == 8
    assert week.source_detail.endswith("period 2026-08-01..2026-08-07 (8 of 20 records)")
    stored = runner.db.read_ecommerce_orders(week.run_id)
    assert len(stored) == 8 and str(stored["order_date"].max())[:10] == "2026-08-07"


def test_run_request_rejects_a_reversed_period():
    with pytest.raises(ValidationError, match="period_to must not be earlier than period_from"):
        RunRequest(pipeline="ecommerce_demo", period_from=date(2026, 8, 7), period_to=date(2026, 8, 1))


def test_run_request_shares_the_period_bounds_of_the_v1_api():
    """Selfhost `POST /run` ma te same granice co v1 `POST .../jobs`: nie przed 2000-01-01, maks. 400 dni (C2-03)."""
    with pytest.raises(ValidationError, match="not be earlier than 2000-01-01"):
        RunRequest(pipeline="ecommerce_demo", period_from=date(1999, 12, 31))
    with pytest.raises(ValidationError, match="longer than 400 days"):
        RunRequest(pipeline="ecommerce_demo", period_from=date(2025, 1, 1), period_to=date(2026, 2, 5))
    ok = RunRequest(pipeline="ecommerce_demo", period_from=date(2025, 1, 1), period_to=date(2026, 2, 4))
    assert (ok.period_to - ok.period_from).days + 1 == 400


def test_filter_period_counts_records_without_a_date_separately():
    """Rekord bez czytelnej daty nie moze nalezec do zakresu; `detail` mowi, ile takich bylo (C2-02)."""
    from app.core.models import DataStatus
    from app.pipelines.runner import _filter_period
    from app.sources.cs2_source import SourceResult

    # zrodla daja `date`/`datetime` (`_day`); napis albo brak pola to rekord bez czytelnej daty
    records = [{"date": date(2026, 8, 1), "order_id": "a"}, {"date": datetime(2026, 8, 9, 10), "order_id": "b"},
               {"date": None, "order_id": "c"}, {"date": "not a date", "order_id": "d"}]
    src = SourceResult(records=records, data_status=DataStatus.LIVE_DATA, detail="demo")
    out = _filter_period(src, date(2026, 8, 1), date(2026, 8, 7))
    assert [r["order_id"] for r in out.records] == ["a"]
    assert out.detail == "demo | period 2026-08-01..2026-08-07 (1 of 4 records, 2 without a date)"
    assert "not a date" not in out.detail
