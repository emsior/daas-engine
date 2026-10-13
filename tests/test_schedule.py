"""Harmonogram organizacji: `compute_next_reminder` (DST), przypomnienia (`send_reminder`, `SchedulerLoop`),
auto-uruchomienie po uploadzie, API `GET/PUT /orgs/{org_id}/schedule` i migracja 0004. Bez sieci: poczta `console`."""
from __future__ import annotations

import asyncio
import io
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.control import models, service
from app.control import schedule as sched
from app.control.db import (
    alembic_config,
    create_engine,
    make_sessionmaker,
    resolve_db_url,
    upgrade_sync,
)
from app.control.mailer import build_mailer
from app.control.models import OrgSchedule
from app.control.plans import PLAN_LIMITS
from app.control.types import utcnow
from app.core.config import Settings
from tests.conftest import PROJECT_ROOT
from tests.v1_helpers import (
    API,
    get_rows,
    key_headers,
    outbox,
    seed_member,
    seed_org,
    update_rows,
    user_headers,
    with_db,
)

WARSAW = "Europe/Warsaw"
CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


# ================================================================ compute_next_reminder
@pytest.mark.parametrize(("after", "expected"), [
    # poniedzialek 12.10.2026 (CEST, UTC+2): przed 8:00 -> dzis 8:00
    (utc(2026, 10, 12, 5, 0), utc(2026, 10, 12, 6, 0)),
    # ten sam dzien, dokladnie o 8:00 lokalnie -> nastepny tydzien (termin musi byc scisle pozniejszy)
    (utc(2026, 10, 12, 6, 0), utc(2026, 10, 19, 6, 0)),
    # ten sam dzien po 8:00 -> nastepny poniedzialek
    (utc(2026, 10, 12, 9, 30), utc(2026, 10, 19, 6, 0)),
    # niedziela wieczorem -> jutro rano
    (utc(2026, 10, 11, 20, 0), utc(2026, 10, 12, 6, 0)),
    # sroda -> poniedzialek po zmianie czasu na zimowy (25.10): 8:00 CET = 7:00 UTC
    (utc(2026, 10, 21, 12, 0), utc(2026, 10, 26, 7, 0)),
    # tydzien przed zmiana na letni: 23.03 8:00 CET = 7:00 UTC
    (utc(2026, 3, 18, 12, 0), utc(2026, 3, 23, 7, 0)),
    # poniedzialek po zmianie na letni (29.03): 30.03 8:00 CEST = 6:00 UTC
    (utc(2026, 3, 24, 12, 0), utc(2026, 3, 30, 6, 0)),
])
def test_monday_8_warsaw_from_different_starting_points(after, expected):
    got = sched.compute_next_reminder(0, 8, WARSAW, after)
    assert got == expected
    assert got.tzinfo is not None and got.utcoffset() == timedelta(0)
    local = got.astimezone(ZoneInfo(WARSAW))
    assert (local.weekday(), local.hour, local.minute) == (0, 8, 0)


def test_naive_after_is_treated_as_utc():
    assert sched.compute_next_reminder(0, 8, WARSAW, datetime(2026, 10, 12, 5, 0)) == utc(2026, 10, 12, 6, 0)


def test_utc_zone():
    assert sched.compute_next_reminder(2, 0, "UTC", utc(2026, 10, 12, 0, 0)) == utc(2026, 10, 14, 0, 0)


def test_hour_that_does_not_exist_on_spring_forward_day():
    """29.03.2026 02:00 w Warszawie nie istnieje (02:00 -> 03:00): termin = pierwsza chwila po przeskoku."""
    got = sched.compute_next_reminder(6, 2, WARSAW, utc(2026, 3, 28, 12, 0))
    assert got == utc(2026, 3, 29, 1, 0)
    assert got.astimezone(ZoneInfo(WARSAW)).hour == 3
    # tydzien pozniej ta godzina juz istnieje (CEST): 05.04 02:00 = 00:00 UTC
    assert sched.compute_next_reminder(6, 2, WARSAW, got) == utc(2026, 4, 5, 0, 0)


def test_hour_that_repeats_on_fall_back_day_fires_once_at_first_occurrence():
    """25.10.2026 02:00 wystepuje dwa razy (CEST, potem CET): bierzemy pierwsze, kolejny termin to tydzien pozniej."""
    first = sched.compute_next_reminder(6, 2, WARSAW, utc(2026, 10, 24, 12, 0))
    assert first == utc(2026, 10, 25, 0, 0)
    second = sched.compute_next_reminder(6, 2, WARSAW, first)
    assert second == utc(2026, 11, 1, 1, 0)  # bez drugiego wystapienia 02:00 CET (01:00 UTC 25.10)


@pytest.mark.parametrize(("weekday", "hour"), [(7, 8), (-1, 8), (0, 24), (0, -1)])
def test_out_of_range_values_are_rejected(weekday, hour):
    with pytest.raises(ValueError):
        sched.compute_next_reminder(weekday, hour, WARSAW, utc(2026, 10, 12))


def test_timezone_validation_uses_the_iana_database():
    assert sched.is_valid_timezone("Europe/Warsaw") and sched.is_valid_timezone("UTC")
    assert not sched.is_valid_timezone("Mars/Olympus") and not sched.is_valid_timezone("")


# ================================================================ przypomnienia
NOW = utc(2026, 10, 12, 6, 0)  # poniedzialek 8:00 w Warszawie


def set_schedule(settings: Settings, org_id: str, **values) -> None:
    async def go(db):
        row = await db.get(OrgSchedule, org_id)
        if row is None:
            row = OrgSchedule(org_id=org_id)
            db.add(row)
        for key, value in {**sched.DEFAULTS, **values}.items():
            setattr(row, key, value)

    with_db(settings, go)


def schedule_row(settings: Settings, org_id: str) -> OrgSchedule:
    (row,) = get_rows(settings, OrgSchedule, OrgSchedule.org_id == org_id)
    return row


def add_upload(settings: Settings, org_id: str, created_at: datetime) -> None:
    upload_id = f"{len(get_rows(settings, models.Upload)):032x}"

    async def go(db):
        db.add(models.Upload(id=upload_id, org_id=org_id, kind="csv", filename="x.csv", bytes=1, sha256="0" * 64,
                             path=f"{upload_id}.csv", created_at=created_at,
                             retention_until=created_at + timedelta(days=90)))

    with_db(settings, go)


async def _tick(settings: Settings, now: datetime) -> list[sched.ReminderResult]:
    engine = create_engine(settings)
    try:
        loop = sched.SchedulerLoop(settings, make_sessionmaker(engine), build_mailer(settings))
        return await loop.tick(now)
    finally:
        await engine.dispose()


def tick(settings: Settings, now: datetime = NOW) -> list[sched.ReminderResult]:
    return asyncio.run(_tick(settings, now))


def schedule_events(settings: Settings, org_id: str, action: str = "schedule.reminder") -> list[models.AuditEvent]:
    return [e for e in get_rows(settings, models.AuditEvent, models.AuditEvent.org_id == org_id)
            if e.action == action]


@pytest.fixture()
def org(cloud_settings: Settings):
    return seed_org(cloud_settings)


def test_due_reminder_goes_to_every_member_in_their_language(cloud_settings, org):
    seed_member(cloud_settings, org.org_id, "admin@example.test", "admin")
    member_id = seed_member(cloud_settings, org.org_id, "member@example.test", "member")
    update_rows(cloud_settings, models.User, {"locale": "en"}, models.User.id == member_id)
    seed_org(cloud_settings, name="Other", owner_email="stranger@example.test")
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=NOW)

    (result,) = tick(cloud_settings)
    assert result.sent is True and result.recipients == 3
    assert {m["to"] for m in outbox(cloud_settings)} == {"owner@example.test", "admin@example.test",
                                                         "member@example.test"}
    (pl,) = outbox(cloud_settings, "owner@example.test")
    (en,) = outbox(cloud_settings, "member@example.test")
    assert "Czas na eksport" in pl["body"] and "raport będzie gotowy po kilku minutach" in pl["body"]
    assert "Time for the export" in en["body"]
    for mail in (pl, en):
        assert mail["link"] == "https://cockpit.example.test/upload"
        assert "token" not in mail["link"] and "token" not in mail["body"]
    (event,) = schedule_events(cloud_settings, org.org_id)
    assert event.meta == {"sent": True, "recipients": 3} and event.actor_type == "system"


def test_next_reminder_moves_one_week_and_last_is_recorded(cloud_settings, org):
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=NOW)
    tick(cloud_settings, NOW + timedelta(seconds=30))
    row = schedule_row(cloud_settings, org.org_id)
    assert row.next_reminder_at == NOW + timedelta(days=7)
    assert row.last_reminder_at == NOW + timedelta(seconds=30)


def test_two_ticks_send_one_mail(cloud_settings, org):
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=NOW)
    tick(cloud_settings)
    assert tick(cloud_settings, NOW + timedelta(minutes=1)) == []
    assert len(outbox(cloud_settings)) == 1
    assert len(schedule_events(cloud_settings, org.org_id)) == 1


def test_concurrent_ticks_claim_the_reminder_once(cloud_settings, org):
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=NOW)

    async def both():
        return await asyncio.gather(_tick(cloud_settings, NOW), _tick(cloud_settings, NOW))

    results = asyncio.run(both())
    assert sum(len(r) for r in results) == 1
    assert len(outbox(cloud_settings)) == 1


def test_recent_upload_skips_the_reminder_but_moves_the_date(cloud_settings, org):
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=NOW)
    add_upload(cloud_settings, org.org_id, NOW - timedelta(days=2))
    (result,) = tick(cloud_settings)
    assert result.sent is False and result.skipped_reason == "recent_upload"
    assert outbox(cloud_settings) == []
    (event,) = schedule_events(cloud_settings, org.org_id)
    assert event.meta == {"sent": False, "recipients": 0, "skipped_reason": "recent_upload"}
    assert schedule_row(cloud_settings, org.org_id).next_reminder_at == NOW + timedelta(days=7)


def test_old_upload_or_disabled_skip_still_sends(cloud_settings, org):
    add_upload(cloud_settings, org.org_id, NOW - timedelta(days=6, minutes=1))
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=NOW)
    assert tick(cloud_settings)[0].sent is True
    add_upload(cloud_settings, org.org_id, NOW)
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, reminder_skip_if_recent_upload=False,
                 next_reminder_at=NOW + timedelta(days=7))
    assert tick(cloud_settings, NOW + timedelta(days=7))[0].sent is True


def test_not_due_disabled_or_missing_schedules_are_ignored(cloud_settings, org):
    other = seed_org(cloud_settings, name="Second", owner_email="second@example.test")
    third = seed_org(cloud_settings, name="Third", owner_email="third@example.test")
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=NOW + timedelta(minutes=1))
    set_schedule(cloud_settings, other.org_id, reminder_enabled=False, next_reminder_at=NOW - timedelta(days=1))
    del third  # bez wiersza harmonogramu

    async def due(db):
        return await sched.due_schedules(db, NOW)

    assert with_db(cloud_settings, due) == []
    assert tick(cloud_settings) == [] and outbox(cloud_settings) == []


def test_missed_reminders_after_downtime_send_once_and_resume_from_now(cloud_settings, org):
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=NOW - timedelta(days=21))
    tick(cloud_settings)
    assert len(outbox(cloud_settings)) == 1
    assert schedule_row(cloud_settings, org.org_id).next_reminder_at == NOW + timedelta(days=7)


def test_one_failing_organization_does_not_stop_the_others(cloud_settings, org, monkeypatch):
    other = seed_org(cloud_settings, name="Second", owner_email="second@example.test")
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=NOW - timedelta(minutes=1))
    set_schedule(cloud_settings, other.org_id, reminder_enabled=True, next_reminder_at=NOW)
    real = sched.send_reminder

    async def flaky(sessionmaker, settings, mailer, org_id, **kw):
        if org_id == org.org_id:
            raise RuntimeError("boom")
        return await real(sessionmaker, settings, mailer, org_id, **kw)

    monkeypatch.setattr(sched, "send_reminder", flaky)
    results = tick(cloud_settings)
    assert [r.org_id for r in results] == [other.org_id]
    assert [m["to"] for m in outbox(cloud_settings)] == ["second@example.test"]


def test_mail_failure_of_one_recipient_does_not_block_the_rest(cloud_settings, org, monkeypatch):
    seed_member(cloud_settings, org.org_id, "b@example.test", "member")
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=NOW)
    from app.control.mailer import ConsoleMailer

    real_send = ConsoleMailer.send

    def send(self, mail):
        if mail.to == "b@example.test":
            raise OSError("smtp down")
        real_send(self, mail)

    monkeypatch.setattr(ConsoleMailer, "send", send)
    (result,) = tick(cloud_settings)
    assert result.sent is True and result.recipients == 1


def test_unknown_stored_timezone_disables_the_reminder_instead_of_retrying_every_tick(cloud_settings, org, caplog):
    # wiersz spoza walidacji PUT (np. strefa usunieta w nowszej bazie tzdata): wstawiony wprost do bazy
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=NOW, timezone="Europe/Atlantis")
    (result,) = tick(cloud_settings)
    assert result.sent is False and result.skipped_reason == "unknown_timezone"
    row = schedule_row(cloud_settings, org.org_id)
    assert row.reminder_enabled is False and row.next_reminder_at is None and row.last_reminder_at == NOW
    assert outbox(cloud_settings) == []
    (event,) = schedule_events(cloud_settings, org.org_id)
    assert event.meta == {"sent": False, "recipients": 0, "skipped_reason": "unknown_timezone"}
    assert "Traceback" not in caplog.text  # ostrzezenie, nie wyjatek
    # kolejne ticki: nic do zrobienia (bez petli bledow w logu)
    assert tick(cloud_settings, NOW + timedelta(minutes=1)) == []
    assert tick(cloud_settings, NOW + timedelta(days=7)) == []
    assert len(schedule_events(cloud_settings, org.org_id)) == 1


def test_org_name_in_the_reminder_is_sanitized(cloud_settings):
    seeded = seed_org(cloud_settings, name="Sklep <b> www.evil.example.test", owner_email="o@example.test")
    set_schedule(cloud_settings, seeded.org_id, reminder_enabled=True, next_reminder_at=NOW)
    tick(cloud_settings)
    (mail,) = outbox(cloud_settings)
    assert "evil" not in mail["body"] and "<b>" not in mail["body"]


# ================================================================ petla w tle
def test_loop_starts_ticks_and_stops_without_leftover_tasks(cloud_settings, org):
    set_schedule(cloud_settings, org.org_id, reminder_enabled=True, next_reminder_at=utcnow() - timedelta(minutes=1))

    async def go():
        engine = create_engine(cloud_settings)
        try:
            loop = sched.SchedulerLoop(cloud_settings, make_sessionmaker(engine), build_mailer(cloud_settings),
                                       tick_sec=5)
            loop.start()
            loop.start()  # idempotentne
            assert loop.running
            for _ in range(100):
                if outbox(cloud_settings):
                    break
                await asyncio.sleep(0.05)
            await loop.stop()
            await loop.stop()  # drugie zatrzymanie nic nie robi
            assert not loop.running
            return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        finally:
            await engine.dispose()

    assert asyncio.run(go()) == []
    assert len(outbox(cloud_settings)) == 1


def test_loop_survives_a_failing_tick(cloud_settings, monkeypatch):
    calls = []

    async def go():
        engine = create_engine(cloud_settings)
        loop = sched.SchedulerLoop(cloud_settings, make_sessionmaker(engine), build_mailer(cloud_settings))
        loop.tick_sec = 0.01  # tylko test: szybkie iteracje

        async def failing(now=None):
            calls.append(now)
            raise RuntimeError("db down")

        loop.tick = failing
        loop.start()
        for _ in range(100):
            if len(calls) >= 3:
                break
            await asyncio.sleep(0.01)
        running = loop.running
        await loop.stop()
        await engine.dispose()
        return running

    assert asyncio.run(go()) is True and len(calls) >= 3


def test_tick_interval_has_a_floor(cloud_settings):
    assert sched.SchedulerLoop(cloud_settings, None, None, tick_sec=1).tick_sec == sched.MIN_TICK_SEC
    assert Settings(_env_file=None).daas_scheduler_tick_sec == 60
    with pytest.raises(ValueError):
        Settings(_env_file=None, daas_scheduler_tick_sec=4)


def test_lifespan_has_no_loop_in_selfhost(client):
    assert client.app.state.scheduler is None


def test_lifespan_runs_the_loop_only_in_cloud_and_when_enabled(cloud_factory):
    enabled, _ = cloud_factory()
    assert isinstance(enabled.app.state.scheduler, sched.SchedulerLoop) and enabled.app.state.scheduler.running
    disabled, _ = cloud_factory(DAAS_SCHEDULER_ENABLED="false")
    assert disabled.app.state.scheduler is None


def test_shutdown_stops_the_loop(cloud_settings):
    from app.main import create_app

    app = create_app(cloud_settings)
    with TestClient(app, base_url="https://testserver"):
        loop = app.state.scheduler
        task = loop._task
        assert loop.running
    assert task.done() and not loop.running


# ================================================================ API
def schedule_url(org_id: str) -> str:
    return f"{API}/orgs/{org_id}/schedule"


VALID = {"reminder_enabled": True, "reminder_weekday": 0, "reminder_hour": 8, "timezone": WARSAW,
         "reminder_skip_if_recent_upload": True, "auto_run_on_upload": False, "notify_on_finish": False}


@pytest.fixture()
def api_org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def test_get_returns_defaults_without_a_row(cloud_client, cloud_settings, api_org):
    r = cloud_client.get(schedule_url(api_org.org_id), headers=key_headers(api_org.api_key))
    assert r.status_code == 200, r.text
    public = {k: v for k, v in sched.DEFAULTS.items() if k != "notify_webhook_url"}  # adres tylko do zapisu
    assert r.json() == {**public, "notify_webhook_set": False, "notify_webhook_hint": None,
                        "next_reminder_at": None, "last_reminder_at": None, "updated_at": None}
    assert get_rows(cloud_settings, OrgSchedule) == []  # GET niczego nie tworzy


def test_put_creates_the_row_computes_the_next_reminder_and_audits(cloud_client, cloud_settings, api_org):
    r = cloud_client.put(schedule_url(api_org.org_id), json=VALID, headers=key_headers(api_org.api_key))
    assert r.status_code == 200, r.text
    body = r.json()
    assert {k: body[k] for k in VALID} == VALID
    nxt = datetime.fromisoformat(body["next_reminder_at"])
    local = nxt.astimezone(ZoneInfo(WARSAW))
    assert (local.weekday(), local.hour) == (0, 8) and nxt > utcnow() and nxt <= utcnow() + timedelta(days=7)
    assert body["updated_at"] and body["last_reminder_at"] is None
    assert cloud_client.get(schedule_url(api_org.org_id), headers=key_headers(api_org.api_key)).json() == body
    (event,) = schedule_events(cloud_settings, api_org.org_id, "schedule.updated")
    assert event.meta == {"fields": ["reminder_enabled"]}


def test_put_updates_and_disabling_clears_the_next_reminder(cloud_client, cloud_settings, api_org):
    hdr = key_headers(api_org.api_key)
    cloud_client.put(schedule_url(api_org.org_id), json=VALID, headers=hdr)
    r = cloud_client.put(schedule_url(api_org.org_id), headers=hdr,
                         json={**VALID, "reminder_enabled": False, "notify_on_finish": True, "timezone": "UTC"})
    assert r.status_code == 200 and r.json()["next_reminder_at"] is None and r.json()["notify_on_finish"] is True
    assert len(get_rows(cloud_settings, OrgSchedule)) == 1
    last = schedule_events(cloud_settings, api_org.org_id, "schedule.updated")[-1]
    assert last.meta == {"fields": ["notify_on_finish", "reminder_enabled", "timezone"]}


def test_put_with_omitted_fields_takes_defaults(cloud_client, cloud_settings, api_org):
    r = cloud_client.put(schedule_url(api_org.org_id), json={"notify_on_finish": True},
                         headers=key_headers(api_org.api_key))
    assert r.status_code == 200 and r.json()["reminder_hour"] == 8 and r.json()["timezone"] == WARSAW


@pytest.mark.parametrize(("field", "value"), [
    ("reminder_weekday", 7), ("reminder_weekday", -1), ("reminder_hour", 24), ("reminder_hour", -1),
    ("timezone", "Mars/Olympus"), ("timezone", ""), ("timezone", "x" * 65), ("reminder_enabled", "maybe"),
])
def test_put_validation_names_the_field(cloud_client, cloud_settings, api_org, field, value):
    r = cloud_client.put(schedule_url(api_org.org_id), json={**VALID, field: value},
                         headers=key_headers(api_org.api_key))
    assert r.status_code == 422, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "validation_error"
    assert any(e["loc"][-1] == field for e in body["errors"]), body
    assert "Mars/Olympus" not in r.text  # wartosc klienta nie jest odbijana
    assert get_rows(cloud_settings, OrgSchedule) == []


def test_member_reads_but_cannot_change_the_schedule(cloud_client, cloud_settings, api_org):
    member = user_headers(cloud_settings, seed_member(cloud_settings, api_org.org_id, "m@example.test", "member"))
    admin = user_headers(cloud_settings, seed_member(cloud_settings, api_org.org_id, "a@example.test", "admin"))
    assert cloud_client.get(schedule_url(api_org.org_id), headers=member).status_code == 200
    r = cloud_client.put(schedule_url(api_org.org_id), json=VALID, headers=member)
    assert r.status_code == 403 and r.json()["code"] == "insufficient_scope"
    assert cloud_client.put(schedule_url(api_org.org_id), json=VALID, headers=admin).status_code == 200


def test_key_scopes_are_enforced(cloud_client, cloud_settings, api_org):
    from tests.v1_helpers import seed_key

    _, read_only = seed_key(cloud_settings, api_org.org_id, scopes=["org:read"])
    _, no_scope = seed_key(cloud_settings, api_org.org_id, scopes=["runs:read"])
    assert cloud_client.get(schedule_url(api_org.org_id), headers=key_headers(read_only)).status_code == 200
    assert cloud_client.put(schedule_url(api_org.org_id), json=VALID,
                            headers=key_headers(read_only)).status_code == 403
    assert cloud_client.get(schedule_url(api_org.org_id), headers=key_headers(no_scope)).status_code == 403


def test_schedule_needs_authentication(cloud_client, api_org):
    assert cloud_client.get(schedule_url(api_org.org_id)).status_code == 401
    assert cloud_client.put(schedule_url(api_org.org_id), json=VALID).status_code == 401


def test_schedules_are_isolated_between_organizations(cloud_client, cloud_settings, api_org):
    other = seed_org(cloud_settings, name="B", owner_email="b@example.test")
    cloud_client.put(schedule_url(other.org_id), json={**VALID, "reminder_hour": 6},
                     headers=key_headers(other.api_key))
    r = cloud_client.put(schedule_url(other.org_id), json=VALID, headers=key_headers(api_org.api_key))
    assert r.status_code == 404
    assert cloud_client.get(schedule_url(other.org_id), headers=key_headers(api_org.api_key)).status_code == 404
    assert schedule_row(cloud_settings, other.org_id).reminder_hour == 6


def test_deleting_the_organization_removes_its_schedule(cloud_client, cloud_settings, api_org):
    cloud_client.put(schedule_url(api_org.org_id), json=VALID, headers=key_headers(api_org.api_key))
    owner = user_headers(cloud_settings, api_org.user_id)
    assert cloud_client.delete(f"{API}/orgs/{api_org.org_id}", headers=owner).status_code == 204
    assert get_rows(cloud_settings, OrgSchedule) == []


# ================================================================ auto-uruchomienie po uploadzie
def upload(client: TestClient, org, name: str = "dane.csv", data: bytes = CSV_BYTES):
    return client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                       files={"file": (name, io.BytesIO(data), "application/octet-stream")})


def wait_job(client: TestClient, org, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"{API}/orgs/{org.org_id}/jobs/{job_id}", headers=key_headers(org.api_key)).json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(job)


def test_without_auto_run_the_upload_has_no_job(cloud_client, cloud_settings, api_org):
    r = upload(cloud_client, api_org)
    assert r.status_code == 201 and r.json()["auto_job_id"] is None
    assert get_rows(cloud_settings, models.Job) == []


def test_auto_run_queues_the_suggested_pipeline_and_it_succeeds(cloud_client, cloud_settings, api_org):
    cloud_client.put(schedule_url(api_org.org_id), json={**VALID, "auto_run_on_upload": True},
                     headers=key_headers(api_org.api_key))
    r = upload(cloud_client, api_org)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["auto_job_id"] and body["suggested_pipeline"] == "ecommerce_demo"
    (job,) = get_rows(cloud_settings, models.Job)
    assert (job.id, job.upload_id, job.pipeline) == (body["auto_job_id"], body["id"], "ecommerce_demo")
    assert job.created_by == f"key:{api_org.key_id}"
    done = wait_job(cloud_client, api_org, job.id)
    assert done["status"] == "succeeded", done
    created = [e for e in get_rows(cloud_settings, models.AuditEvent) if e.action == "job.created"]
    assert created[0].meta["reason"] == "auto_run" and created[0].meta["upload_id"] == body["id"]


def test_auto_run_job_is_queued_through_the_executor(cloud_client, cloud_settings, api_org):
    held: list[tuple[str, str]] = []
    cloud_client.app.state.executor.submit = lambda job_id, org_id: held.append((job_id, org_id))
    cloud_client.put(schedule_url(api_org.org_id), json={**VALID, "auto_run_on_upload": True},
                     headers=key_headers(api_org.api_key))
    body = upload(cloud_client, api_org).json()
    assert held == [(body["auto_job_id"], api_org.org_id)]
    (job,) = get_rows(cloud_settings, models.Job)
    assert job.status == "queued"


def test_plan_limit_keeps_the_upload_and_skips_the_job(cloud_client, cloud_settings, api_org):
    cloud_client.put(schedule_url(api_org.org_id), json={**VALID, "auto_run_on_upload": True},
                     headers=key_headers(api_org.api_key))

    async def exhaust(db):
        db.add(models.UsageCounter(org_id=api_org.org_id, period=service.current_period(),
                                   runs=PLAN_LIMITS["free"].runs_per_month, upload_bytes=0))

    with_db(cloud_settings, exhaust)
    r = upload(cloud_client, api_org)
    assert r.status_code == 201, r.text
    assert r.json()["auto_job_id"] is None
    assert len(get_rows(cloud_settings, models.Upload)) == 1 and get_rows(cloud_settings, models.Job) == []
    (event,) = schedule_events(cloud_settings, api_org.org_id, "schedule.auto_run_skipped")
    assert event.meta == {"upload_id": r.json()["id"], "pipeline": "ecommerce_demo", "reason": "plan_limit"}


def test_full_queue_keeps_the_upload_and_skips_the_job(cloud_client, cloud_settings, api_org):
    from app.api.v1.jobs import MAX_PENDING_JOBS
    from tests.v1_helpers import seed_job_row

    cloud_client.app.state.executor.submit = lambda job_id, org_id: None
    update_rows(cloud_settings, models.Org, {"plan": "pro"}, models.Org.id == api_org.org_id)
    for _ in range(MAX_PENDING_JOBS):
        seed_job_row(cloud_settings, api_org.org_id)
    cloud_client.put(schedule_url(api_org.org_id), json={**VALID, "auto_run_on_upload": True},
                     headers=key_headers(api_org.api_key))
    r = upload(cloud_client, api_org)
    assert r.status_code == 201 and r.json()["auto_job_id"] is None
    (event,) = schedule_events(cloud_settings, api_org.org_id, "schedule.auto_run_skipped")
    assert event.meta["reason"] == "queue_full"


def test_auto_run_skips_a_header_only_csv(cloud_client, cloud_settings, api_org):
    cloud_client.put(schedule_url(api_org.org_id), json={**VALID, "auto_run_on_upload": True},
                     headers=key_headers(api_org.api_key))
    r = upload(cloud_client, api_org, data="Data sprzeda\u017cy;Warto\u015b\u0107 brutto\n".encode())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["rows"] == 0 and body["ready"] is False and body["auto_job_id"] is None
    assert get_rows(cloud_settings, models.Job) == []  # run skonczylby sie no_records (i mailem o bledzie)
    (event,) = schedule_events(cloud_settings, api_org.org_id, "schedule.auto_run_skipped")
    assert event.meta == {"upload_id": body["id"], "pipeline": "ecommerce_demo", "reason": "upload_not_ready"}


def test_auto_run_needs_the_runs_write_scope_of_the_caller(cloud_client, cloud_settings, api_org):
    from tests.v1_helpers import seed_key

    cloud_client.app.state.executor.submit = lambda job_id, org_id: None
    cloud_client.put(schedule_url(api_org.org_id), json={**VALID, "auto_run_on_upload": True},
                     headers=key_headers(api_org.api_key))
    _, upload_only = seed_key(cloud_settings, api_org.org_id, scopes=["uploads:write"], name="collector")
    _, upload_and_run = seed_key(cloud_settings, api_org.org_id, scopes=["uploads:write", "runs:write"])

    r = cloud_client.post(f"{API}/orgs/{api_org.org_id}/uploads", headers=key_headers(upload_only),
                          files={"file": ("dane.csv", io.BytesIO(CSV_BYTES), "application/octet-stream")})
    assert r.status_code == 201 and r.json()["auto_job_id"] is None, r.text
    assert get_rows(cloud_settings, models.Job) == []
    (event,) = schedule_events(cloud_settings, api_org.org_id, "schedule.auto_run_skipped")
    assert event.meta["reason"] == "insufficient_scope" and event.actor_type == "key"

    r = cloud_client.post(f"{API}/orgs/{api_org.org_id}/uploads", headers=key_headers(upload_and_run),
                          files={"file": ("dane.csv", io.BytesIO(CSV_BYTES), "application/octet-stream")})
    assert r.status_code == 201 and r.json()["auto_job_id"], r.text
    (job,) = get_rows(cloud_settings, models.Job)
    assert job.id == r.json()["auto_job_id"]


def test_auto_run_counts_against_the_run_rate_limit_of_the_caller(cloud_factory):
    client, settings = cloud_factory(DAAS_RATE_LIMIT_RUN_PER_MIN=1)
    seeded = seed_org(settings)
    client.app.state.executor.submit = lambda job_id, org_id: None
    client.put(schedule_url(seeded.org_id), json={**VALID, "auto_run_on_upload": True},
               headers=key_headers(seeded.api_key))
    first, second = (upload(client, seeded).json() for _ in range(2))
    assert first["auto_job_id"] and second["auto_job_id"] is None
    assert len(get_rows(settings, models.Upload)) == 2 and len(get_rows(settings, models.Job)) == 1
    (event,) = schedule_events(settings, seeded.org_id, "schedule.auto_run_skipped")
    assert event.meta["reason"] == "rate_limited"


def test_auto_run_failure_before_queueing_keeps_the_committed_upload(cloud_client, cloud_settings, api_org,
                                                                     monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    cloud_client.put(schedule_url(api_org.org_id), json={**VALID, "auto_run_on_upload": True},
                     headers=key_headers(api_org.api_key))
    real_get = AsyncSession.get

    async def broken_get(self, entity, *args, **kwargs):
        if entity is OrgSchedule:
            raise RuntimeError("control db hiccup")
        return await real_get(self, entity, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", broken_get)
    r = upload(cloud_client, api_org)
    assert r.status_code == 201, r.text  # upload jest juz zapisany: blad auto-runu nie zamienia go w 422
    body = r.json()
    assert body["auto_job_id"] is None and get_rows(cloud_settings, models.Job) == []
    (row,) = get_rows(cloud_settings, models.Upload)
    stored = cloud_client.app.state.tenants.paths(api_org.org_id).uploads / row.path
    assert row.id == body["id"] and stored.is_file()  # plik nie zostal usuniety
    (event,) = schedule_events(cloud_settings, api_org.org_id, "schedule.auto_run_skipped")
    assert event.meta["reason"] == "internal_error"


def test_auto_run_for_a_collector_package_uses_its_pipeline(cloud_client, cloud_settings, api_org):
    cloud_client.app.state.executor.submit = lambda job_id, org_id: None
    cloud_client.put(schedule_url(api_org.org_id), json={**VALID, "auto_run_on_upload": True},
                     headers=key_headers(api_org.api_key))
    package = (PROJECT_ROOT / "data" / "fixtures" / "uksc" / "host_compliant.json").read_bytes()
    body = upload(cloud_client, api_org, "paczka.json", package).json()
    (job,) = get_rows(cloud_settings, models.Job)
    assert job.id == body["auto_job_id"] and job.pipeline == "uksc_evidence"


# ================================================================ migracja 0004
def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, daas_control_db_url=f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")


def _tables_and_indexes(settings: Settings) -> tuple[set[str], set[str]]:
    async def go(db):
        def read(sync):
            insp = inspect(sync.connection())
            names = set(insp.get_table_names())
            indexes = {i["name"] for i in insp.get_indexes("org_schedules")} if "org_schedules" in names else set()
            return names, indexes

        return await db.run_sync(read)

    return with_db(settings, go)


def test_migration_0004_creates_the_table_and_reverts_cleanly(tmp_path: Path):
    from alembic import command

    s = _settings(tmp_path)
    upgrade_sync(s, "0003")
    assert "org_schedules" not in _tables_and_indexes(s)[0]
    upgrade_sync(s)
    tables, indexes = _tables_and_indexes(s)
    assert "org_schedules" in tables and "ix_org_schedules_due" in indexes

    async def fk(db):
        return (await db.execute(text("PRAGMA foreign_key_list(org_schedules)"))).all()

    (row,) = with_db(s, fk)
    assert row[2] == "orgs" and row[6] == "CASCADE"
    command.downgrade(alembic_config(resolve_db_url(s)), "0003")
    assert "org_schedules" not in _tables_and_indexes(s)[0]
    assert "run_records" in _tables_and_indexes(s)[0]


def test_migration_0004_renders_for_postgres(tmp_path: Path):
    """Skladnia dla Postgresa: tryb offline Alembica (`--sql`) generuje DDL bez polaczenia z baza."""
    from alembic import command

    cfg = alembic_config("postgresql://user:pass@localhost/daas")
    buffer = io.StringIO()
    cfg.output_buffer = buffer
    command.upgrade(cfg, "0003:0004", sql=True)
    ddl = buffer.getvalue()
    assert "CREATE TABLE org_schedules" in ddl
    assert "FOREIGN KEY(org_id) REFERENCES orgs (id) ON DELETE CASCADE" in ddl
    assert "reminder_enabled BOOLEAN NOT NULL" in ddl and "next_reminder_at TIMESTAMP WITHOUT TIME ZONE" in ddl
    assert "CREATE INDEX ix_org_schedules_due ON org_schedules (reminder_enabled, next_reminder_at)" in ddl
    assert "UPDATE alembic_version SET version_num='0004'" in ddl


# ================================================================ migracja 0005 (OAuth, webhook czatu, okres joba)
def test_migration_0005_adds_oauth_tables_and_columns_and_reverts_cleanly(tmp_path: Path):
    from alembic import command

    s = _settings(tmp_path)
    upgrade_sync(s, "0004")
    tables, _ = _tables_and_indexes(s)
    assert "oauth_tokens" not in tables
    upgrade_sync(s)
    tables, _ = _tables_and_indexes(s)
    assert {"oauth_clients", "oauth_codes", "oauth_tokens"} <= tables

    async def columns(db):
        schedule = {r[1] for r in (await db.execute(text("PRAGMA table_info(org_schedules)"))).all()}
        jobs = {r[1] for r in (await db.execute(text("PRAGMA table_info(jobs)"))).all()}
        tokens = {(r[2], r[6]) for r in (await db.execute(text("PRAGMA foreign_key_list(oauth_tokens)"))).all()}
        indexes = {r[1] for r in (await db.execute(text("PRAGMA index_list(oauth_tokens)"))).all()}
        indexes |= {r[1] for r in (await db.execute(text("PRAGMA index_list(oauth_codes)"))).all()}
        return schedule, jobs, tokens, indexes

    schedule, jobs, token_fks, indexes = with_db(s, columns)
    assert {"ix_oauth_tokens_org_id_kind", "ix_oauth_codes_expires_at"} <= indexes
    assert {"notify_webhook_url", "notify_webhook_kind"} <= schedule
    assert {"period_from", "period_to"} <= jobs
    assert ("orgs", "CASCADE") in token_fks and ("oauth_clients", "CASCADE") in token_fks
    command.downgrade(alembic_config(resolve_db_url(s)), "0004")
    tables, _ = _tables_and_indexes(s)
    assert "oauth_tokens" not in tables and "org_schedules" in tables


def test_migration_0005_renders_for_postgres(tmp_path: Path):
    from alembic import command

    cfg = alembic_config("postgresql://user:pass@localhost/daas")
    buffer = io.StringIO()
    cfg.output_buffer = buffer
    command.upgrade(cfg, "0004:0005", sql=True)
    ddl = buffer.getvalue()
    assert "CREATE TABLE oauth_tokens" in ddl and "ALTER TABLE org_schedules ADD COLUMN notify_webhook_url" in ddl
