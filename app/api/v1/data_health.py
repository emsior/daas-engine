"""Pulpit "zdrowie danych": czy rytm eksport -> raport w poniedzialek trwa (`GET /orgs/{org_id}/data-health`).

Liczone w locie z wierszy control plane (uploady, joby, runy, harmonogram, zuzycie planu); nic nie jest zapisywane,
trasa jest tylko do odczytu (bez audytu). Tygodnie sa tygodniami ISO w strefie harmonogramu organizacji: wiadro
zaczyna sie w poniedzialek 00:00 czasu lokalnego, a granice liczy `zoneinfo` (zmiana czasu nie przesuwa wiader).

Podsumowanie i problemy (`issues`) zawsze patrza na ostatnie `LOOKBACK_WEEKS` tygodni, niezaleznie od parametru
`weeks` (ten wybiera tylko, ile tygodni oddac w tabeli): przelaczenie zakresu w kokpicie nie zmienia werdyktu.
Stala liczba zapytan (bez N+1); grupowanie po tygodniach w Pythonie, bo strefy w SQL sa rozne w SQLite i Postgresie.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import OrgContext, org_access
from app.api.v1.schemas import DataHealthOut, DataHealthSummary, HealthIssue, WeekHealth
from app.api.v1.usage import usage_report
from app.control import schedule as sched
from app.control import scopes as sc
from app.control.db import get_db
from app.control.models import Job, Org, OrgSchedule, RunRecord, Upload, UsageCounter
from app.control.types import utcnow

router = APIRouter(tags=["data-health"])

_READ = org_access(sc.RUNS_READ)

DEFAULT_WEEKS = 12
MAX_WEEKS = 26
#: Okno danych podsumowania i problemow: stale, niezalezne od `weeks`.
LOOKBACK_WEEKS = MAX_WEEKS
#: Brak uploadu od tylu dni = `stale_upload`.
STALE_UPLOAD_AFTER = timedelta(days=7)
#: `plan_limit_near` od 4/5 limitu runow (licznik, mianownik: bez ulamkow zmiennoprzecinkowych).
NEAR_LIMIT = (4, 5)
#: `partial_reports`: raport PARTIAL w tylu ostatnich tygodniach (biezacy + 3 poprzednie).
PARTIAL_WINDOW_WEEKS = 4
FINISHED_JOB = ("succeeded", "failed")
REPORT_STATUSES = ("SUCCESS", "PARTIAL")
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


# ----------------------------------------------------------------------------- tygodnie w strefie (czyste funkcje)
def zone_of(name: str | None) -> ZoneInfo:
    """Strefa harmonogramu; pusta albo nieznana (np. usunieta z nowszej bazy tzdata) = `Europe/Warsaw`."""
    try:
        return ZoneInfo(name or sched.DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(sched.DEFAULT_TIMEZONE)


def week_start(day: date) -> date:
    """Poniedzialek tygodnia ISO, do ktorego nalezy `day`."""
    return day - timedelta(days=day.weekday())


def week_of(instant: datetime, zone: ZoneInfo) -> date:
    """Poniedzialek (data lokalna w `zone`) tygodnia, w ktorym wypada chwila `instant`; naiwna chwila = UTC."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return week_start(instant.astimezone(zone).date())


def week_starts(now: datetime, zone: ZoneInfo, count: int) -> list[date]:
    """`count` poniedzialkow od najstarszego do biezacego tygodnia (daty lokalne w `zone`)."""
    current = week_of(now, zone)
    return [current - timedelta(weeks=back) for back in range(count - 1, -1, -1)]


def local_midnight(day: date, zone: ZoneInfo) -> datetime:
    """Poczatek dnia `day` w `zone` jako chwila UTC. Polnoc, ktorej nie ma (przeskok czasu o polnocy), wypada
    w pierwszej chwili po przeskoku — ta sama konwencja co `compute_next_reminder`."""
    return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)


@dataclass
class _Bucket:
    uploads: int = 0
    upload_bytes: int = 0
    runs_succeeded: int = 0
    runs_failed: int = 0
    reports: int = 0
    run_seconds: float = 0.0
    timed_runs: int = 0
    last_report_at: datetime | None = None


def bucket_weeks(starts: list[date], zone: ZoneInfo, *, uploads: Iterable[tuple[datetime, int]],
                 jobs: Iterable[tuple[str, datetime | None, datetime]],
                 reports: Iterable[tuple[str, datetime]]) -> list[WeekHealth]:
    """Wiadra tygodni `starts` (ostatni = biezacy, czesciowy). Wejscie: uploady `(created_at, bytes)`, zakonczone
    joby `(status, started_at, finished_at)` (tydzien wg `finished_at`), raporty `(status, created_at)`.
    Zdarzenia spoza tygodni `starts` sa pomijane."""
    buckets = {start: _Bucket() for start in starts}
    for created_at, size in uploads:
        bucket = buckets.get(week_of(created_at, zone))
        if bucket is not None:
            bucket.uploads += 1
            bucket.upload_bytes += size or 0
    for status, started_at, finished_at in jobs:
        bucket = buckets.get(week_of(finished_at, zone))
        if bucket is None:
            continue
        if status == "succeeded":
            bucket.runs_succeeded += 1
        else:
            bucket.runs_failed += 1
        if started_at is not None:
            bucket.run_seconds += max(0.0, (finished_at - started_at).total_seconds())
            bucket.timed_runs += 1
    for _status, created_at in reports:
        bucket = buckets.get(week_of(created_at, zone))
        if bucket is not None:
            bucket.reports += 1
            if bucket.last_report_at is None or created_at > bucket.last_report_at:
                bucket.last_report_at = created_at
    out: list[WeekHealth] = []
    for start in starts:
        b = buckets[start]
        out.append(WeekHealth(
            week_start=start, partial=start == starts[-1], uploads=b.uploads, upload_bytes=b.upload_bytes,
            runs_succeeded=b.runs_succeeded, runs_failed=b.runs_failed, reports=b.reports,
            avg_run_seconds=round(b.run_seconds / b.timed_runs, 1) if b.timed_runs else None,
            last_report_at=b.last_report_at))
    return out


def streak_weeks(weeks: list[WeekHealth]) -> int:
    """Kolejne zakonczone tygodnie z co najmniej jednym raportem, liczac wstecz od poprzedniego tygodnia."""
    count = 0
    for week in reversed(weeks[:-1]):  # biezacy (czesciowy) tydzien nie przerywa i nie wydluza serii
        if week.reports == 0:
            break
        count += 1
    return count


@dataclass(frozen=True)
class _Failure:
    finished_at: datetime
    error_code: str | None
    run_id: str | None


def build_issues(*, now: datetime, summary: DataHealthSummary, last_failure: _Failure | None,
                 uploaded_before: bool, first_partial_at: datetime | None) -> list[HealthIssue]:
    """Reguly problemow; wynik od najpowazniejszych (error -> warning -> info), w obrebie poziomu w kolejnosci regul."""
    issues: list[HealthIssue] = []

    def add(code: str, severity: str, link: str, since: datetime | None = None) -> None:
        issues.append(HealthIssue(code=code, severity=severity, since=since, link=link))

    if last_failure is not None and (summary.last_success_at is None
                                     or last_failure.finished_at > summary.last_success_at):
        add("last_run_failed", "error", f"/runs/{last_failure.run_id}" if last_failure.run_id else "/upload",
            last_failure.finished_at)
    limit, used = summary.runs_limit, summary.runs_used
    if limit is not None and used >= limit:
        add("plan_limit_reached", "error", "/settings")
    elif limit is not None and used * NEAR_LIMIT[1] >= limit * NEAR_LIMIT[0]:
        add("plan_limit_near", "warning", "/settings")
    if summary.last_upload_at is not None:
        if now - summary.last_upload_at >= STALE_UPLOAD_AFTER:
            add("stale_upload", "warning", "/upload", summary.last_upload_at)
    elif uploaded_before:  # rekordy uploadow usunela retencja: plik byl, ale dawno
        add("stale_upload", "warning", "/upload")
    else:
        add("no_uploads_yet", "info", "/upload")
    if first_partial_at is not None:
        add("partial_reports", "warning", "/", first_partial_at)
    if not summary.reminder_enabled:
        add("reminder_off", "info", "/settings")
    if not summary.auto_run_on_upload:
        add("auto_run_off", "info", "/settings")
    return sorted(issues, key=lambda issue: _SEVERITY_RANK[issue.severity])  # sortowanie stabilne


# ----------------------------------------------------------------------------- logika wspolna (v1; gotowa dla /mcp)
async def data_health(db: AsyncSession, org: Org, *, weeks: int = DEFAULT_WEEKS,
                      now: datetime | None = None) -> DataHealthOut:
    """Pulpit zdrowia danych organizacji: `weeks` ostatnich tygodni (1-26) + podsumowanie i problemy (26 tygodni)."""
    now = now or utcnow()
    schedule = await db.get(OrgSchedule, org.id) or sched.default_schedule(org.id)
    zone = zone_of(schedule.timezone)
    starts = week_starts(now, zone, LOOKBACK_WEEKS)
    since = local_midnight(starts[0], zone)

    uploads = (await db.execute(select(Upload.created_at, Upload.bytes).where(
        Upload.org_id == org.id, Upload.created_at >= since))).all()
    jobs = (await db.execute(select(Job.status, Job.started_at, Job.finished_at).where(
        Job.org_id == org.id, Job.status.in_(FINISHED_JOB), Job.finished_at >= since))).all()
    # raport = run SUCCESS/PARTIAL, ktorego job nie skonczyl sie bledem (PARTIAL bez rekordow to `no_records`)
    reports = (await db.execute(
        select(RunRecord.status, RunRecord.created_at)
        .outerjoin(Job, (Job.id == RunRecord.job_id) & (Job.org_id == RunRecord.org_id))
        .where(RunRecord.org_id == org.id, RunRecord.created_at >= since, RunRecord.status.in_(REPORT_STATUSES),
               or_(Job.id.is_(None), Job.status != "failed")))).all()

    last_upload_at = (await db.execute(select(Upload.created_at).where(Upload.org_id == org.id)
                                       .order_by(Upload.created_at.desc()).limit(1))).scalar()
    last_success_at = (await db.execute(select(Job.finished_at).where(
        Job.org_id == org.id, Job.status == "succeeded", Job.finished_at.is_not(None))
        .order_by(Job.finished_at.desc()).limit(1))).scalar()
    failure_row = (await db.execute(select(Job.finished_at, Job.error_code, Job.run_id).where(
        Job.org_id == org.id, Job.status == "failed", Job.finished_at.is_not(None))
        .order_by(Job.finished_at.desc(), Job.id.desc()).limit(1))).first()
    last_failure = _Failure(*failure_row) if failure_row is not None else None
    uploaded_before = last_upload_at is not None or bool((await db.execute(select(exists().where(
        UsageCounter.org_id == org.id, UsageCounter.upload_bytes > 0)))).scalar())
    usage = await usage_report(db, org)

    all_weeks = bucket_weeks(starts, zone, uploads=uploads, jobs=jobs, reports=reports)
    window = starts[-PARTIAL_WINDOW_WEEKS]
    partials = [created_at for status, created_at in reports
                if status == "PARTIAL" and window <= week_of(created_at, zone) <= starts[-1]]
    summary = DataHealthSummary(
        last_upload_at=last_upload_at, last_success_at=last_success_at,
        last_failure_at=last_failure.finished_at if last_failure else None,
        last_error_code=last_failure.error_code if last_failure else None,
        streak_weeks=streak_weeks(all_weeks), reminder_enabled=schedule.reminder_enabled,
        next_reminder_at=schedule.next_reminder_at if schedule.reminder_enabled else None,
        auto_run_on_upload=schedule.auto_run_on_upload, notify_on_finish=schedule.notify_on_finish,
        plan=org.plan, runs_used=usage.current.runs, runs_limit=usage.limits.runs_per_month,
        retention_days=org.retention_days)
    issues = build_issues(now=now, summary=summary, last_failure=last_failure, uploaded_before=uploaded_before,
                          first_partial_at=min(partials) if partials else None)
    return DataHealthOut(generated_at=now, timezone=zone.key, weeks=all_weeks[-weeks:], summary=summary,
                         issues=issues)


# ----------------------------------------------------------------------------- trasa
@router.get(
    "/orgs/{org_id}/data-health", response_model=DataHealthOut, summary="Data health dashboard",
    description="Is next Monday's report safe? Per ISO week in the time zone of the organization's schedule "
                "(Monday 00:00 local; the last week is the current, partial one): uploads, finished jobs, "
                "reports and the mean run time. The summary has the last upload, success and failure, the streak "
                "of finished weeks with a report, the reminder and auto-run settings and the plan usage; `issues` "
                "lists what to fix, most severe first, each with a cockpit link. Computed on the fly, read-only. "
                "`weeks` (1-26, default 12) only limits the `weeks` list: the summary and the issues always look "
                "at the last 26 weeks.",
    responses={422: {"description": "`weeks` outside 1-26"}},
)
async def get_data_health(
    weeks: int = Query(default=DEFAULT_WEEKS, ge=1, le=MAX_WEEKS,
                       description="how many weeks to return, the current one included (1-26)"),
    ctx: OrgContext = Depends(_READ), db: AsyncSession = Depends(get_db),
) -> DataHealthOut:
    return await data_health(db, ctx.org, weeks=weeks)
