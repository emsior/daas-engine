"""Pulpit zdrowia danych: `GET /orgs/{org_id}/data-health` — tygodnie ISO w strefie harmonogramu (granice i DST),
wiadra uploadow/jobow/raportow, seria tygodni z raportem, reguly `issues`, parametr `weeks`, zakres `runs:read`.
Izolacja organizacji: macierz w `test_tenant_isolation.py` obejmuje trase automatycznie; tutaj dodatkowo dane."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.api.v1 import data_health as dh
from app.control import models, service
from app.control.db import create_engine, make_sessionmaker
from app.control.ids import new_ulid
from app.core.config import Settings
from tests.v1_helpers import (
    API,
    get_rows,
    key_headers,
    run,
    seed_key,
    seed_member,
    seed_org,
    update_rows,
    user_headers,
    with_db,
)

WARSAW = ZoneInfo("Europe/Warsaw")


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


#: Sroda 14.10.2026 12:00 w Warszawie (CEST, UTC+2): biezacy tydzien zaczyna sie w poniedzialek 12.10.
NOW = utc(2026, 10, 14, 10, 0)


def url(org_id: str) -> str:
    return f"{API}/orgs/{org_id}/data-health"


@pytest.fixture()
def org(cloud_settings: Settings):
    return seed_org(cloud_settings)


# ----------------------------------------------------------------------------- zasiewanie wierszy
def add_upload(settings: Settings, org_id: str, created_at: datetime, size: int = 1) -> str:
    upload_id = uuid.uuid4().hex

    async def go(db):
        db.add(models.Upload(id=upload_id, org_id=org_id, kind="csv", filename="x.csv", bytes=size, sha256="0" * 64,
                             path=f"{upload_id}.csv", created_at=created_at,
                             retention_until=created_at + timedelta(days=90)))

    with_db(settings, go)
    return upload_id


def add_job(settings: Settings, org_id: str, status: str, finished_at: datetime, *, seconds: float = 30.0,
            error_code: str | None = None, run_status: str | None = None) -> tuple[str, str | None]:
    """Zakonczony job (i, gdy podano `run_status`, jego rekord runu z chwila zakonczenia). Zwraca `(job_id, run_id)`."""
    job_id = new_ulid()
    run_id = str(uuid.uuid4()) if run_status else None

    async def go(db):
        db.add(models.Job(id=job_id, org_id=org_id, pipeline="ecommerce_demo", status=status, progress=100,
                          run_id=run_id, error_code=error_code, created_at=finished_at - timedelta(seconds=seconds + 5),
                          started_at=finished_at - timedelta(seconds=seconds), finished_at=finished_at))
        if run_status:
            db.add(models.RunRecord(run_id=run_id, org_id=org_id, pipeline="ecommerce_demo", status=run_status,
                                    job_id=job_id, created_at=finished_at))

    with_db(settings, go)
    return job_id, run_id


def set_usage(settings: Settings, org_id: str, period: str, *, runs: int = 0, upload_bytes: int = 0) -> None:
    async def go(db):
        db.add(models.UsageCounter(org_id=org_id, period=period, runs=runs, upload_bytes=upload_bytes))

    with_db(settings, go)


def set_schedule(settings: Settings, org_id: str, **values) -> None:
    async def go(db):
        row = models.OrgSchedule(org_id=org_id, **values)
        db.add(row)

    with_db(settings, go)


def health(settings: Settings, org_id: str, *, weeks: int = 12, now: datetime = NOW):
    async def go(db):
        org = await db.get(models.Org, org_id)
        return await dh.data_health(db, org, weeks=weeks, now=now)

    return with_db(settings, go)


def codes(out) -> list[str]:
    return [issue.code for issue in out.issues]


# ================================================================ tygodnie w strefie (czyste funkcje)
def test_week_starts_are_local_mondays_oldest_first():
    assert dh.week_starts(NOW, WARSAW, 3) == [date(2026, 9, 28), date(2026, 10, 5), date(2026, 10, 12)]
    assert dh.week_starts(utc(2026, 10, 12, 6, 0), WARSAW, 1) == [date(2026, 10, 12)]  # poniedzialek rano


def test_sunday_2330_utc_is_monday_0130_in_warsaw():
    assert dh.week_of(utc(2026, 10, 11, 23, 30), WARSAW) == date(2026, 10, 12)
    assert dh.week_of(utc(2026, 10, 11, 21, 59), WARSAW) == date(2026, 10, 5)  # niedziela 23:59 CEST
    assert dh.local_midnight(date(2026, 10, 12), WARSAW) == utc(2026, 10, 11, 22, 0)
    assert dh.week_of(datetime(2026, 10, 11, 23, 30), WARSAW) == date(2026, 10, 12)  # naiwna chwila = UTC


@pytest.mark.parametrize(("monday", "start_utc", "hours"), [
    (date(2026, 10, 19), utc(2026, 10, 18, 22, 0), 169),  # zmiana na czas zimowy 25.10: tydzien ma 169 h
    (date(2026, 3, 23), utc(2026, 3, 22, 23, 0), 167),    # zmiana na czas letni 29.03: tydzien ma 167 h
    (date(2026, 10, 12), utc(2026, 10, 11, 22, 0), 168),
])
def test_week_bounds_follow_dst(monday, start_utc, hours):
    start = dh.local_midnight(monday, WARSAW)
    end = dh.local_midnight(monday + timedelta(weeks=1), WARSAW)
    assert start == start_utc and end - start == timedelta(hours=hours)
    assert dh.week_of(start, WARSAW) == monday
    assert dh.week_of(end - timedelta(seconds=1), WARSAW) == monday
    assert dh.week_of(end, WARSAW) == monday + timedelta(weeks=1)


def test_unknown_or_empty_zone_falls_back_to_warsaw():
    assert dh.zone_of("Mars/Olympus").key == "Europe/Warsaw"
    assert dh.zone_of("").key == "Europe/Warsaw"
    assert dh.zone_of(None).key == "Europe/Warsaw"
    assert dh.zone_of("UTC").key == "UTC"


def test_streak_skips_the_current_week_and_stops_at_the_first_gap():
    def weeks(*reports: int):
        empty = dh.bucket_weeks(dh.week_starts(NOW, WARSAW, len(reports)), WARSAW, uploads=[], jobs=[], reports=[])
        return [week.model_copy(update={"reports": n}) for week, n in zip(empty, reports, strict=True)]

    assert dh.streak_weeks(weeks(1, 0, 2, 1, 0)) == 2
    assert dh.streak_weeks(weeks(1, 1, 1, 1, 5)) == 4  # biezacy tydzien nie wydluza serii
    assert dh.streak_weeks(weeks(1, 1, 1, 0, 0)) == 0  # biezacy tydzien bez raportu jeszcze jej nie przerywa
    assert dh.streak_weeks(weeks(0)) == 0


# ================================================================ API: ksztalt, parametr, zakres
def test_empty_organization_is_all_zeros_with_info_issues(cloud_client, cloud_settings, org):
    r = cloud_client.get(url(org.org_id), headers=key_headers(org.api_key))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["timezone"] == "Europe/Warsaw" and body["generated_at"]
    weeks = body["weeks"]
    assert len(weeks) == 12
    assert [w["partial"] for w in weeks] == [False] * 11 + [True]
    starts = [date.fromisoformat(w["week_start"]) for w in weeks]
    assert all(d.weekday() == 0 for d in starts)
    assert all(b - a == timedelta(weeks=1) for a, b in zip(starts, starts[1:], strict=False))
    for week in weeks:
        assert (week["uploads"], week["upload_bytes"], week["runs_succeeded"], week["runs_failed"],
                week["reports"]) == (0, 0, 0, 0, 0)
        assert week["avg_run_seconds"] is None and week["last_report_at"] is None
    assert body["summary"] == {
        "last_upload_at": None, "last_success_at": None, "last_failure_at": None, "last_error_code": None,
        "streak_weeks": 0, "reminder_enabled": False, "next_reminder_at": None, "auto_run_on_upload": False,
        "notify_on_finish": False, "plan": "free", "runs_used": 0, "runs_limit": 20, "retention_days": 90}
    assert body["issues"] == [
        {"code": "no_uploads_yet", "severity": "info", "since": None, "link": "/upload"},
        {"code": "reminder_off", "severity": "info", "since": None, "link": "/settings"},
        {"code": "auto_run_off", "severity": "info", "since": None, "link": "/settings"},
    ]


@pytest.mark.parametrize("weeks", [1, 8, 26])
def test_weeks_parameter_limits_the_list(cloud_client, org, weeks):
    r = cloud_client.get(url(org.org_id), params={"weeks": weeks}, headers=key_headers(org.api_key))
    assert r.status_code == 200, r.text
    listed = r.json()["weeks"]
    assert len(listed) == weeks and listed[-1]["partial"] is True
    assert sum(w["partial"] for w in listed) == 1


@pytest.mark.parametrize("weeks", ["0", "27", "-1", "abc"])
@pytest.mark.parametrize(
    "request_id", [None, "TEST-abc-request"], ids=["generated-id", "id-containing-input"]
)
def test_weeks_outside_1_26_is_422_naming_the_parameter(cloud_client, org, weeks, request_id):
    headers = key_headers(org.api_key)
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    r = cloud_client.get(url(org.org_id), params={"weeks": weeks}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    if request_id is not None:
        assert body["request_id"] == request_id
    assert body["code"] == "validation_error"
    assert any(e["loc"] == ["query", "weeks"] for e in body["errors"]), body
    redacted_body = {key: value for key, value in body.items() if key != "request_id"}
    assert "abc" not in json.dumps(redacted_body)  # wartosc klienta nie jest odbijana


def test_scope_runs_read_is_required(cloud_client, cloud_settings, org):
    _, upload_only = seed_key(cloud_settings, org.org_id, scopes=["uploads:write"], name="collector")
    _, runs_read = seed_key(cloud_settings, org.org_id, scopes=["runs:read"], name="reader")
    r = cloud_client.get(url(org.org_id), headers=key_headers(upload_only))
    assert r.status_code == 403 and r.json()["code"] == "insufficient_scope"
    assert r.json()["required_scope"] == "runs:read"
    assert cloud_client.get(url(org.org_id), headers=key_headers(runs_read)).status_code == 200


def test_member_reads_and_strangers_get_404(cloud_client, cloud_settings, org):
    member = user_headers(cloud_settings, seed_member(cloud_settings, org.org_id, "m@example.test", "member"))
    assert cloud_client.get(url(org.org_id), headers=member).status_code == 200
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    r = cloud_client.get(url(other.org_id), headers=key_headers(org.api_key))
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert cloud_client.get(url(org.org_id)).status_code == 401


def test_reading_the_dashboard_is_not_audited(cloud_client, cloud_settings, org):
    before = len(get_rows(cloud_settings, models.AuditEvent))
    for weeks in (8, 12, 26):
        assert cloud_client.get(url(org.org_id), params={"weeks": weeks},
                                headers=key_headers(org.api_key)).status_code == 200
    assert len(get_rows(cloud_settings, models.AuditEvent)) == before


def test_buckets_follow_the_schedule_zone(cloud_settings, org):
    """Upload w niedziele 23:30 UTC: w Warszawie to juz poniedzialek (nowy tydzien), w Nowym Jorku jeszcze niedziela."""
    sunday_night = utc(2026, 10, 11, 23, 30)
    add_upload(cloud_settings, org.org_id, sunday_night, size=10)
    in_warsaw = health(cloud_settings, org.org_id)
    assert [w.uploads for w in in_warsaw.weeks[-2:]] == [0, 1]
    set_schedule(cloud_settings, org.org_id, timezone="America/New_York")
    in_new_york = health(cloud_settings, org.org_id)
    assert in_new_york.timezone == "America/New_York"
    assert [w.uploads for w in in_new_york.weeks[-2:]] == [1, 0]
    assert in_new_york.weeks[-1].week_start == date(2026, 10, 12)


# ================================================================ wiadra, seria, podsumowanie
def seed_history(settings: Settings, org_id: str) -> None:
    """Piec ostatnich tygodni (Warszawa): biezacy 12.10, 05.10, 28.09, 21.09 (pusty), 14.09."""
    add_upload(settings, org_id, utc(2026, 10, 11, 23, 30), size=100)  # niedziela 23:30 UTC = pon. 01:30 CEST
    add_upload(settings, org_id, utc(2026, 10, 13, 8, 0), size=50)
    add_job(settings, org_id, "succeeded", utc(2026, 10, 13, 8, 2), seconds=30, run_status="SUCCESS")
    add_upload(settings, org_id, utc(2026, 10, 11, 21, 30), size=7)  # niedziela 23:30 CEST: jeszcze 05.10
    # zrodlo bez rekordow: job `failed/no_records`, a jego run ma status PARTIAL (executor, C9)
    add_job(settings, org_id, "failed", utc(2026, 10, 6, 9, 0), seconds=10, error_code="no_records",
            run_status="PARTIAL")
    add_job(settings, org_id, "succeeded", utc(2026, 10, 7, 9, 0), seconds=90, run_status="SUCCESS")
    add_job(settings, org_id, "succeeded", utc(2026, 9, 30, 9, 0), seconds=20, run_status="PARTIAL")
    add_job(settings, org_id, "succeeded", utc(2026, 9, 16, 9, 0), seconds=40, run_status="SUCCESS")


def test_weeks_count_uploads_jobs_and_reports_in_local_weeks(cloud_settings, org):
    seed_history(cloud_settings, org.org_id)
    out = health(cloud_settings, org.org_id)
    assert out.timezone == "Europe/Warsaw" and out.generated_at == NOW
    current, last, two_ago, gap, four_ago = out.weeks[-1], out.weeks[-2], out.weeks[-3], out.weeks[-4], out.weeks[-5]
    assert [w.week_start for w in out.weeks[-5:]] == [date(2026, 9, 14), date(2026, 9, 21), date(2026, 9, 28),
                                                     date(2026, 10, 5), date(2026, 10, 12)]
    assert current.partial and not last.partial
    assert (current.uploads, current.upload_bytes, current.runs_succeeded, current.runs_failed, current.reports) == (
        2, 150, 1, 0, 1)
    assert current.avg_run_seconds == 30.0 and current.last_report_at == utc(2026, 10, 13, 8, 2)
    # no_records: job jest bledem (runs_failed), a jego run PARTIAL nie jest raportem
    assert (last.uploads, last.upload_bytes, last.runs_succeeded, last.runs_failed, last.reports) == (1, 7, 1, 1, 1)
    assert last.avg_run_seconds == 50.0 and last.last_report_at == utc(2026, 10, 7, 9, 0)
    assert (two_ago.reports, two_ago.runs_succeeded, two_ago.avg_run_seconds) == (1, 1, 20.0)
    assert (gap.reports, gap.uploads, gap.runs_succeeded, gap.avg_run_seconds) == (0, 0, 0, None)
    assert four_ago.reports == 1
    assert out.summary.streak_weeks == 2  # 05.10 i 28.09; pusty 21.09 przerywa serie


def test_summary_and_issues_of_a_healthy_history_with_a_partial_report(cloud_settings, org):
    seed_history(cloud_settings, org.org_id)
    out = health(cloud_settings, org.org_id)
    s = out.summary
    assert s.last_upload_at == utc(2026, 10, 13, 8, 0)
    assert s.last_success_at == utc(2026, 10, 13, 8, 2)
    assert (s.last_failure_at, s.last_error_code) == (utc(2026, 10, 6, 9, 0), "no_records")
    assert (s.plan, s.runs_used, s.runs_limit, s.retention_days) == ("free", 0, 20, 90)
    # starszy blad przykryty pozniejszym sukcesem: bez `last_run_failed`; PARTIAL z 30.09 jest w oknie 4 tygodni
    assert [(i.code, i.severity, i.link, i.since) for i in out.issues] == [
        ("partial_reports", "warning", "/", utc(2026, 9, 30, 9, 0)),
        ("reminder_off", "info", "/settings", None),
        ("auto_run_off", "info", "/settings", None),
    ]


def test_weeks_parameter_does_not_change_the_summary_or_the_issues(cloud_settings, org):
    seed_history(cloud_settings, org.org_id)
    short, full = health(cloud_settings, org.org_id, weeks=1), health(cloud_settings, org.org_id, weeks=26)
    assert len(short.weeks) == 1 and len(full.weeks) == 26
    assert short.weeks[0] == full.weeks[-1]
    assert short.summary == full.summary and short.issues == full.issues


def test_other_organizations_rows_are_not_counted(cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    seed_history(cloud_settings, other.org_id)
    set_usage(cloud_settings, other.org_id, service.current_period(), runs=19, upload_bytes=10)
    out = health(cloud_settings, org.org_id, weeks=26)
    assert all((w.uploads, w.runs_succeeded, w.runs_failed, w.reports) == (0, 0, 0, 0) for w in out.weeks)
    assert out.summary.last_upload_at is None and out.summary.last_failure_at is None
    assert codes(out) == ["no_uploads_yet", "reminder_off", "auto_run_off"]


def test_run_pointing_at_a_job_of_another_org_still_counts(cloud_settings, org):
    """Zlaczenie run -> job jest w obrebie organizacji: obcy job `failed` nie wycina raportu."""
    _, run_id = add_job(cloud_settings, org.org_id, "succeeded", utc(2026, 10, 13, 9, 0), run_status="SUCCESS")
    assert health(cloud_settings, org.org_id).weeks[-1].reports == 1
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    foreign_job, _ = add_job(cloud_settings, other.org_id, "failed", utc(2026, 10, 13, 9, 0), error_code="x")
    update_rows(cloud_settings, models.RunRecord, {"job_id": foreign_job}, models.RunRecord.run_id == run_id)
    assert health(cloud_settings, org.org_id).weeks[-1].reports == 1


def test_legacy_run_without_a_job_is_a_report(cloud_settings, org):
    async def go(db):
        db.add(models.RunRecord(run_id=str(uuid.uuid4()), org_id=org.org_id, pipeline="ecommerce_demo",
                                status="SUCCESS", job_id=None, created_at=utc(2026, 10, 12, 7, 0)))

    with_db(cloud_settings, go)
    out = health(cloud_settings, org.org_id)
    assert out.weeks[-1].reports == 1 and out.weeks[-1].runs_succeeded == 0


# ================================================================ reguly issues
def test_last_run_failed_links_to_the_run_and_comes_first(cloud_settings, org):
    add_upload(cloud_settings, org.org_id, utc(2026, 10, 13, 8, 0))
    add_job(cloud_settings, org.org_id, "succeeded", utc(2026, 10, 12, 9, 0), run_status="SUCCESS")
    _, run_id = add_job(cloud_settings, org.org_id, "failed", utc(2026, 10, 13, 9, 0), error_code="RUN_FAILED",
                        run_status="FAILED")
    set_usage(cloud_settings, org.org_id, service.current_period(), runs=16)
    out = health(cloud_settings, org.org_id)
    first = out.issues[0]
    assert (first.code, first.severity, first.link, first.since) == (
        "last_run_failed", "error", f"/runs/{run_id}", utc(2026, 10, 13, 9, 0))
    assert out.summary.last_error_code == "RUN_FAILED"
    assert codes(out) == ["last_run_failed", "plan_limit_near", "reminder_off", "auto_run_off"]
    assert out.weeks[-1].reports == 1 and out.weeks[-1].runs_failed == 1  # run FAILED nie jest raportem


def test_failure_without_a_run_links_to_upload(cloud_settings, org):
    add_job(cloud_settings, org.org_id, "failed", utc(2026, 10, 13, 9, 0), error_code="SOURCE_UNAVAILABLE")
    out = health(cloud_settings, org.org_id)
    (issue,) = [i for i in out.issues if i.code == "last_run_failed"]
    assert issue.link == "/upload" and out.summary.last_error_code == "SOURCE_UNAVAILABLE"


def test_a_later_success_clears_last_run_failed(cloud_settings, org):
    add_job(cloud_settings, org.org_id, "failed", utc(2026, 10, 12, 9, 0), error_code="RUN_FAILED")
    add_job(cloud_settings, org.org_id, "succeeded", utc(2026, 10, 13, 9, 0), run_status="SUCCESS")
    out = health(cloud_settings, org.org_id)
    assert "last_run_failed" not in codes(out)
    assert out.summary.last_failure_at == utc(2026, 10, 12, 9, 0)  # podsumowanie pamieta ostatni blad


@pytest.mark.parametrize(("plan", "runs", "expected"), [
    ("free", 15, None),
    ("free", 16, ("plan_limit_near", "warning")),
    ("free", 19, ("plan_limit_near", "warning")),
    ("free", 20, ("plan_limit_reached", "error")),
    ("free", 25, ("plan_limit_reached", "error")),
    ("pro", 400, ("plan_limit_near", "warning")),
    ("selfhost", 10**6, None),
])
def test_plan_limit_issues(cloud_settings, org, plan, runs, expected):
    update_rows(cloud_settings, models.Org, {"plan": plan}, models.Org.id == org.org_id)
    set_usage(cloud_settings, org.org_id, service.current_period(), runs=runs)
    out = health(cloud_settings, org.org_id)
    found = [(i.code, i.severity) for i in out.issues if i.code.startswith("plan_limit")]
    assert found == ([expected] if expected else [])
    assert all(i.link == "/settings" for i in out.issues if i.code.startswith("plan_limit"))
    assert out.summary.runs_used == runs and out.summary.plan == plan
    assert out.summary.runs_limit == {"free": 20, "pro": 500, "selfhost": None}[plan]


@pytest.mark.parametrize(("age", "stale"), [
    (timedelta(days=8), True), (timedelta(days=7), True), (timedelta(days=6, hours=23), False),
])
def test_stale_upload_after_seven_days(cloud_settings, org, age, stale):
    add_upload(cloud_settings, org.org_id, NOW - age)
    out = health(cloud_settings, org.org_id)
    found = [i for i in out.issues if i.code == "stale_upload"]
    assert bool(found) is stale and "no_uploads_yet" not in codes(out)
    if stale:
        assert (found[0].severity, found[0].link, found[0].since) == ("warning", "/upload", NOW - age)


def test_uploads_purged_by_retention_are_stale_not_never(cloud_settings, org):
    set_usage(cloud_settings, org.org_id, "2026-05", upload_bytes=1234)  # byl upload, rekord usunela retencja
    out = health(cloud_settings, org.org_id)
    (issue,) = [i for i in out.issues if i.code == "stale_upload"]
    assert issue.since is None and "no_uploads_yet" not in codes(out)
    assert out.summary.last_upload_at is None


def test_partial_report_older_than_four_weeks_is_not_an_issue(cloud_settings, org):
    add_upload(cloud_settings, org.org_id, utc(2026, 10, 13, 8, 0))
    add_job(cloud_settings, org.org_id, "succeeded", utc(2026, 9, 18, 9, 0), run_status="PARTIAL")  # tydzien 14.09
    assert "partial_reports" not in codes(health(cloud_settings, org.org_id))
    add_job(cloud_settings, org.org_id, "succeeded", utc(2026, 9, 21, 9, 0), run_status="PARTIAL")  # tydzien 21.09
    (issue,) = [i for i in health(cloud_settings, org.org_id).issues if i.code == "partial_reports"]
    assert issue.since == utc(2026, 9, 21, 9, 0) and issue.link == "/"


def test_schedule_on_removes_the_info_issues_and_fills_the_summary(cloud_settings, org):
    add_upload(cloud_settings, org.org_id, utc(2026, 10, 13, 8, 0))
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, auto_run_on_upload=True, notify_on_finish=True,
                 next_reminder_at=utc(2026, 10, 19, 6, 0))
    out = health(cloud_settings, org.org_id)
    assert out.issues == []
    s = out.summary
    assert (s.reminder_enabled, s.auto_run_on_upload, s.notify_on_finish) == (True, True, True)
    assert s.next_reminder_at == utc(2026, 10, 19, 6, 0)


def test_unknown_stored_zone_falls_back_to_warsaw(cloud_settings, org):
    set_schedule(cloud_settings, org.org_id, timezone="Europe/Atlantis")
    out = health(cloud_settings, org.org_id)
    assert out.timezone == "Europe/Warsaw" and out.weeks[-1].week_start == date(2026, 10, 12)


def test_issue_order_is_error_then_warning_then_info():
    summary = dh.DataHealthSummary(
        last_upload_at=None, last_success_at=None, last_failure_at=NOW, last_error_code="x",
        streak_weeks=0, reminder_enabled=False, next_reminder_at=None, auto_run_on_upload=False,
        notify_on_finish=False, plan="free", runs_used=20, runs_limit=20, retention_days=90)
    issues = dh.build_issues(now=NOW, summary=summary, last_failure=dh._Failure(NOW, "x", None),
                             uploaded_before=False, first_partial_at=NOW)
    # `no_uploads_yet` (info) jest sprawdzane przed `partial_reports` (warning), a w wyniku stoi za nim
    assert [(i.code, i.severity) for i in issues] == [
        ("last_run_failed", "error"), ("plan_limit_reached", "error"), ("partial_reports", "warning"),
        ("no_uploads_yet", "info"), ("reminder_off", "info"), ("auto_run_off", "info")]
    stale = dh.build_issues(now=NOW, summary=summary.model_copy(update={"last_upload_at": NOW - timedelta(days=30)}),
                            last_failure=None, uploaded_before=True, first_partial_at=None)
    assert [i.code for i in stale] == ["plan_limit_reached", "stale_upload", "reminder_off", "auto_run_off"]


# ================================================================ stala liczba zapytan (bez N+1)
def test_query_count_does_not_grow_with_the_data(cloud_settings, org):
    async def count(org_id: str) -> int:
        engine = create_engine(cloud_settings)
        statements: list[str] = []

        def on_execute(conn, cursor, statement, *args):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", on_execute)
        try:
            async with make_sessionmaker(engine)() as db:
                row = await db.get(models.Org, org_id)
                statements.clear()
                await dh.data_health(db, row, weeks=26, now=NOW)
        finally:
            await engine.dispose()
        return len(statements)

    async def seed(db):
        for day in range(60):
            when = NOW - timedelta(days=day, hours=1)
            upload_id, job_id, run_id = uuid.uuid4().hex, new_ulid(), str(uuid.uuid4())
            db.add(models.Upload(id=upload_id, org_id=org.org_id, kind="csv", filename="x.csv", bytes=1,
                                 sha256="0" * 64, path=f"{upload_id}.csv", created_at=when,
                                 retention_until=when + timedelta(days=90)))
            db.add(models.Job(id=job_id, org_id=org.org_id, pipeline="ecommerce_demo", run_id=run_id,
                              status="succeeded" if day % 3 else "failed", created_at=when, started_at=when,
                              finished_at=when + timedelta(seconds=5)))
            db.add(models.RunRecord(run_id=run_id, org_id=org.org_id, pipeline="ecommerce_demo", status="SUCCESS",
                                    job_id=job_id, created_at=when + timedelta(seconds=5)))

    empty = run(count(org.org_id))
    with_db(cloud_settings, seed)
    assert sum(w.uploads for w in health(cloud_settings, org.org_id, weeks=26).weeks) == 60
    seeded = run(count(org.org_id))
    assert seeded <= empty <= 10  # z danymi odpada tylko zapytanie "czy byl kiedys upload"


def test_http_answer_matches_the_service(cloud_client: TestClient, cloud_settings, org, monkeypatch):
    seed_history(cloud_settings, org.org_id)
    monkeypatch.setattr(dh, "utcnow", lambda: NOW)
    body = cloud_client.get(url(org.org_id), headers=key_headers(org.api_key)).json()
    assert body == health(cloud_settings, org.org_id).model_dump(mode="json")
    assert body["weeks"][-1]["week_start"] == "2026-10-12" and body["summary"]["streak_weeks"] == 2
