"""Powiadomienie e-mail po zakonczeniu runu (`notify_on_finish`): sukces i blad, metadane bez tresci raportu,
link do runu bez tokenu, jedna wysylka na run (listener `run.finished`), audyt `schedule.notified`."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control.mailer import run_finished_mail
from app.core.config import Settings
from app.sources.ecommerce_source import EcommerceSource
from tests.conftest import PROJECT_ROOT
from tests.v1_helpers import API, get_rows, key_headers, outbox, seed_member, seed_org, update_rows

CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()
SECRET_CLIENT_NAME = "Tajna Firma Sp. z o.o."
#: Wartosci z pliku klienta (fixture): nie moga trafic do maila.
CUSTOMER_VALUES = ("Fotel biurowy ERGO", "Biurko regulowane", "899,00", "2026/08/0001", "Meble")
SCHEDULE = {"reminder_enabled": False, "reminder_weekday": 0, "reminder_hour": 8, "timezone": "Europe/Warsaw",
            "reminder_skip_if_recent_upload": True, "auto_run_on_upload": False, "notify_on_finish": True}


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    seeded = seed_org(cloud_settings)
    r = cloud_client.put(f"{API}/orgs/{seeded.org_id}/schedule", json=SCHEDULE, headers=key_headers(seeded.api_key))
    assert r.status_code == 200, r.text
    return seeded


def wait_for(predicate, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def run_job(client: TestClient, org, body: dict) -> dict:
    r = client.post(f"{API}/orgs/{org.org_id}/jobs", json=body, headers=key_headers(org.api_key))
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]

    def done():
        job = client.get(f"{API}/orgs/{org.org_id}/jobs/{job_id}", headers=key_headers(org.api_key)).json()
        return job if job["status"] in ("succeeded", "failed", "cancelled") else None

    return wait_for(done)


def upload(client: TestClient, org) -> str:
    r = client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                    files={"file": ("dane.csv", CSV_BYTES, "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def notified(settings: Settings, org_id: str) -> list[models.AuditEvent]:
    return [e for e in get_rows(settings, models.AuditEvent, models.AuditEvent.org_id == org_id)
            if e.action == "schedule.notified"]


def test_success_mail_has_metadata_hash_and_link_but_no_report_content(cloud_client, cloud_settings, org):
    job = run_job(cloud_client, org, {"pipeline": "ecommerce_demo", "upload_id": upload(cloud_client, org),
                                      "client_name": SECRET_CLIENT_NAME})
    assert job["status"] == "succeeded", job
    (mail,) = wait_for(lambda: outbox(cloud_settings, org.email))
    (record,) = get_rows(cloud_settings, models.RunRecord, models.RunRecord.run_id == job["run_id"])
    assert mail["subject"] == "Raport jest gotowy"
    assert mail["link"] == f"https://cockpit.example.test/runs/{job['run_id']}"
    body = mail["body"]
    assert "ecommerce_demo" in body and "zakończony" in body
    assert record.sha256_html and record.sha256_html in body
    assert record.period_from and record.period_from.isoformat() in body and record.period_to.isoformat() in body
    assert "Kod błędu" not in body
    for forbidden in (SECRET_CLIENT_NAME, "Tajna", "token", "<html", *CUSTOMER_VALUES):
        assert forbidden not in body, forbidden
    (event,) = wait_for(lambda: notified(cloud_settings, org.org_id))
    assert event.meta == {"run_id": job["run_id"], "job_id": job["id"], "status": "succeeded", "sent": True,
                          "recipients": 1}


def test_failure_mail_carries_the_error_code(cloud_client, cloud_settings, org, monkeypatch):
    def broken(self, force_mock=False, source_path=None):
        raise ValueError("Fotel biurowy ERGO: parse error")  # tresc wyjatku (z danymi) nie moze trafic do maila

    monkeypatch.setattr(EcommerceSource, "fetch", broken)
    job = run_job(cloud_client, org, {"pipeline": "ecommerce_demo", "upload_id": upload(cloud_client, org)})
    assert job["status"] == "failed" and job["error_code"], job
    (mail,) = wait_for(lambda: outbox(cloud_settings, org.email))
    assert mail["subject"] == "Przetwarzanie raportu nie powiodło się"
    assert f"Kod błędu: {job['error_code']}" in mail["body"] and "błąd" in mail["body"]
    assert "Fotel" not in mail["body"] and "parse error" not in mail["body"]
    assert mail["link"].endswith(f"/runs/{job['run_id']}")
    (event,) = wait_for(lambda: notified(cloud_settings, org.org_id))
    assert event.meta["status"] == "failed"


def test_every_member_gets_one_mail_in_their_language(cloud_client, cloud_settings, org):
    member_id = seed_member(cloud_settings, org.org_id, "member@example.test", "member")
    update_rows(cloud_settings, models.User, {"locale": "en"}, models.User.id == member_id)
    seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    job = run_job(cloud_client, org, {"pipeline": "cs2_demo", "force_mock": True})
    wait_for(lambda: notified(cloud_settings, org.org_id))
    mails = outbox(cloud_settings)
    assert sorted(m["to"] for m in mails) == sorted([org.email, "member@example.test"])
    (en,) = outbox(cloud_settings, "member@example.test")
    assert en["subject"] == "Your report is ready" and "cs2_demo" in en["body"]
    assert "Data period" not in en["body"]  # cs2_demo nie ma okresu danych
    assert en["link"].endswith(f"/runs/{job['run_id']}")


def test_one_run_one_notification(cloud_client, cloud_settings, org):
    for _ in range(2):
        run_job(cloud_client, org, {"pipeline": "cs2_demo", "force_mock": True})
    wait_for(lambda: len(notified(cloud_settings, org.org_id)) == 2)
    time.sleep(0.2)
    assert len(outbox(cloud_settings, org.email)) == 2
    assert len({e.meta["run_id"] for e in notified(cloud_settings, org.org_id)}) == 2


def test_no_mail_when_the_option_is_off(cloud_client, cloud_settings, org):
    cloud_client.put(f"{API}/orgs/{org.org_id}/schedule", json={**SCHEDULE, "notify_on_finish": False},
                     headers=key_headers(org.api_key))
    run_job(cloud_client, org, {"pipeline": "cs2_demo", "force_mock": True})
    time.sleep(0.3)
    assert outbox(cloud_settings) == [] and notified(cloud_settings, org.org_id) == []


def test_no_mail_without_a_schedule_row(cloud_client, cloud_settings):
    seeded = seed_org(cloud_settings, name="Plain", owner_email="plain@example.test")
    run_job(cloud_client, seeded, {"pipeline": "cs2_demo", "force_mock": True})
    time.sleep(0.3)
    assert outbox(cloud_settings) == []


def test_mail_failure_does_not_fail_the_job(cloud_client, cloud_settings, org, monkeypatch):
    def boom(mail):
        raise OSError("smtp down")

    monkeypatch.setattr(cloud_client.app.state.mailer, "send", boom)
    job = run_job(cloud_client, org, {"pipeline": "cs2_demo", "force_mock": True})
    assert job["status"] == "succeeded"
    (event,) = wait_for(lambda: notified(cloud_settings, org.org_id))
    assert event.meta["sent"] is False and event.meta["recipients"] == 0


def test_partial_run_mail_says_partial_in_both_languages():
    settings = Settings(_env_file=None, daas_cockpit_origin="https://cockpit.example.test")
    common = dict(org_name="Firma", pipeline="uksc_evidence", run_id="r-2", period=(None, None),
                  report_sha256="a" * 64, error_code=None)
    pl = run_finished_mail(settings, "a@example.test", "pl", succeeded=True, partial=True, **common)
    en = run_finished_mail(settings, "a@example.test", "en", succeeded=True, partial=True, **common)
    assert pl.subject == "Raport jest gotowy (częściowy)" and "Status: zakończony częściowo" in pl.body
    assert en.subject == "Your report is ready (partial)" and "Status: partial" in en.body
    full = run_finished_mail(settings, "a@example.test", "pl", succeeded=True, **common)
    assert full.subject == "Raport jest gotowy" and "częściow" not in full.body
    failed = run_finished_mail(settings, "a@example.test", "pl", succeeded=False, partial=True, **common)
    assert failed.subject == "Przetwarzanie raportu nie powiodło się"  # porazka ma pierwszenstwo


def test_partial_run_status_reaches_the_notification(cloud_settings, org):
    import asyncio

    from app.control.db import create_engine, make_sessionmaker
    from app.control.executor import RunFinished
    from app.control.mailer import build_mailer
    from app.control.notifications import RunNotifier

    event = RunFinished(org_id=org.org_id, job_id="01JOB", run_id="run-partial", pipeline="uksc_evidence",
                        status="succeeded", run_status="PARTIAL", finished_at_iso="2026-10-12T08:00:00+00:00")

    async def go():
        engine = create_engine(cloud_settings)
        try:
            notifier = RunNotifier(cloud_settings, make_sessionmaker(engine), build_mailer(cloud_settings))
            await notifier.on_run_finished(event)
            await notifier.drain()
        finally:
            await engine.dispose()

    asyncio.run(go())
    (mail,) = outbox(cloud_settings)
    assert mail["subject"] == "Raport jest gotowy (częściowy)" and mail["link"].endswith("/runs/run-partial")
    (audit,) = notified(cloud_settings, org.org_id)
    assert audit.meta["status"] == "succeeded" and audit.meta["sent"] is True


def test_template_sanitizes_the_org_name_and_has_no_token():
    settings = Settings(_env_file=None, daas_cockpit_origin="https://cockpit.example.test/")
    mail = run_finished_mail(settings, "a@example.test", "pl", org_name="Firma https://evil.example.test <x>",
                             pipeline="ecommerce_demo", succeeded=False, run_id="r-1", period=(None, None),
                             report_sha256=None, error_code="RUN_FAILED")
    assert mail.link == "https://cockpit.example.test/runs/r-1"
    assert "evil" not in mail.body and "<x>" not in mail.body and "token" not in mail.body
    assert "Kod błędu: RUN_FAILED" in mail.body and "SHA-256" not in mail.body and "Okres" not in mail.body
