"""Maintenance preserves restored state and prevents run/notification side effects."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control.executor import RunFinished
from app.control.ids import new_ulid
from app.control.security import sha256_hex, webhook_signing_secret
from app.control.service import current_period
from app.control.types import utcnow
from app.core.config import Settings
from app.main import create_app
from tests.conftest import auth_headers
from tests.mcp_helpers import call_tool, error_code, ok
from tests.test_jobs import CSV_BYTES, seed_job
from tests.v1_helpers import API, get_rows, key_headers, seed_org, with_db


@pytest.fixture()
def maintenance_settings(cloud_settings, monkeypatch):
    monkeypatch.setenv("DAAS_MAINTENANCE_MODE", "true")
    return Settings(_env_file=None)


def _org(settings):
    return seed_org(settings, name="TestBrand", owner_email="owner@example.invalid")


def _hook(settings, org_id):
    hook_id = new_ulid()
    secret = webhook_signing_secret(hook_id, settings.daas_secret_key)

    async def seed(db):
        hook = models.Webhook(id=hook_id, org_id=org_id, url="https://receiver.example.invalid/events",
                              events=["run.finished"], secret_hash=sha256_hex(secret), active=True)
        db.add(hook)
        return hook

    return with_db(settings, seed)


def _event(org_id):
    return RunFinished(org_id, new_ulid(), new_ulid(), "ecommerce_demo", "succeeded", "SUCCESS",
                       utcnow().isoformat())


@pytest.mark.parametrize("enabled", [False, True])
def test_health_reports_configured_maintenance_without_changing_liveness(cloud_settings, monkeypatch, enabled):
    monkeypatch.setenv("DAAS_MAINTENANCE_MODE", str(enabled).lower())
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get(f"{API}/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["maintenance"] is enabled


@pytest.mark.parametrize("status", ["queued", "running"])
def test_startup_preserves_restored_jobs(maintenance_settings, status):
    org = _org(maintenance_settings)
    job_id = seed_job(maintenance_settings, org.org_id, status=status)
    with TestClient(create_app(maintenance_settings)) as client:
        async def settle():
            if client.app.state.executor._tasks:
                await asyncio.gather(*list(client.app.state.executor._tasks))

        client.portal.call(settle)
        (job,) = get_rows(maintenance_settings, models.Job, models.Job.id == job_id)
        assert job.status == status
        assert job.run_id is None
        assert not get_rows(maintenance_settings, models.RunRecord)


@pytest.mark.parametrize("worker", ["scheduler", "retention_task"])
def test_maintenance_does_not_start_background_workers(maintenance_settings, worker):
    with TestClient(create_app(maintenance_settings)) as client:
        assert getattr(client.app.state, worker) is None


@pytest.mark.parametrize("exhausted", [False, True])
def test_maintenance_rejects_new_jobs_without_writing_a_job(maintenance_settings, exhausted):
    org = _org(maintenance_settings)
    if exhausted:
        async def seed(db):
            db.add(models.UsageCounter(org_id=org.org_id, period=current_period(), runs=100000))

        with_db(maintenance_settings, seed)
    with TestClient(create_app(maintenance_settings)) as client:
        response = client.post(f"{API}/orgs/{org.org_id}/jobs", headers=key_headers(org.api_key),
                               json={"pipeline": "ecommerce_demo", "force_mock": True})
        assert response.status_code == 503
        assert response.json()["code"] == "maintenance"
        assert response.headers["content-type"].startswith("application/problem+json")
        assert not get_rows(maintenance_settings, models.Job)


def test_mcp_run_cannot_bypass_maintenance(maintenance_settings):
    org = _org(maintenance_settings)
    with TestClient(create_app(maintenance_settings)) as client:
        upload = ok(call_tool(client, org.api_key, "upload_csv",
                              {"filename": "client_export_pl.csv", "content": CSV_BYTES.decode("utf-8-sig")}))
        result = call_tool(client, org.api_key, "start_run",
                           {"upload_id": upload["id"], "pipeline": "ecommerce_demo"})
        assert error_code(result) == "maintenance"
        assert not get_rows(maintenance_settings, models.Job)


def test_upload_does_not_auto_run_in_maintenance(maintenance_settings):
    org = _org(maintenance_settings)

    async def seed(db):
        db.add(models.OrgSchedule(org_id=org.org_id, auto_run_on_upload=True))

    with_db(maintenance_settings, seed)
    with TestClient(create_app(maintenance_settings)) as client:
        response = client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                               files={"file": ("client_export_pl.csv", CSV_BYTES, "text/csv")})
        assert response.status_code == 201
        assert response.json()["auto_job_id"] is None
        assert not get_rows(maintenance_settings, models.Job)


@pytest.mark.parametrize("entry", ["start", "submit", "execute"])
def test_executor_cannot_bypass_maintenance(maintenance_settings, entry):
    org = _org(maintenance_settings)
    with TestClient(create_app(maintenance_settings)) as client:
        job_id = seed_job(maintenance_settings, org.org_id)
        executor = client.app.state.executor

        async def attempt():
            if entry == "start":
                await executor.start()
            elif entry == "submit":
                executor.submit(job_id, org.org_id)
            else:
                await executor._execute(job_id, org.org_id)
            if executor._tasks:
                await asyncio.gather(*list(executor._tasks))

        client.portal.call(attempt)
        (job,) = get_rows(maintenance_settings, models.Job, models.Job.id == job_id)
        assert job.status == "queued"
        assert not get_rows(maintenance_settings, models.RunRecord)


def test_selfhost_run_is_blocked_before_pipeline_execution(settings, monkeypatch):
    monkeypatch.setenv("DAAS_MAINTENANCE_MODE", "true")
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.post("/run", headers=auth_headers(),
                               json={"pipeline": "ecommerce_demo", "force_mock": True})
        assert response.status_code == 503
        assert response.json()["code"] == "maintenance"
        assert client.app.state.runner.db.latest_run("ecommerce_demo") is None


@pytest.mark.parametrize("entry", ["ping", "listener", "deliver"])
def test_webhooks_never_contact_destination_in_maintenance(maintenance_settings, monkeypatch, entry):
    calls = []

    async def sender(*args, **kwargs):
        calls.append(args)
        return 204, None

    monkeypatch.setattr("app.control.webhooks.post_json", sender)
    org = _org(maintenance_settings)
    hook = _hook(maintenance_settings, org.org_id)
    with TestClient(create_app(maintenance_settings)) as client:
        dispatcher = client.app.state.webhooks
        if entry == "ping":
            response = client.post(f"{API}/orgs/{org.org_id}/webhooks/{hook.id}/ping",
                                   headers=key_headers(org.api_key))
            assert response.status_code == 503
            assert response.json()["code"] == "maintenance"
        elif entry == "listener":
            client.portal.call(dispatcher.on_run_finished, _event(org.org_id))
            client.portal.call(dispatcher.drain)
        else:
            result = client.portal.call(dispatcher.deliver, hook, "ping", b"{}")
            assert not result.delivered
            assert result.attempts == 0
            assert result.error_type == "maintenance"
        assert calls == []
        assert not get_rows(maintenance_settings, models.AuditEvent,
                            models.AuditEvent.action.in_(("webhook.delivered", "webhook.failed")))


@pytest.mark.parametrize("channel", ["mail", "chat"])
@pytest.mark.parametrize("entry", ["listener", "direct"])
def test_run_notifications_do_not_send_in_maintenance(maintenance_settings, monkeypatch, channel, entry):
    calls = []

    async def sender(*args, **kwargs):
        calls.append(args)
        return 204, None

    class MailSink:
        def send(self, message):
            calls.append(message)

    monkeypatch.setattr("app.control.notifications.post_json", sender)
    org = _org(maintenance_settings)

    async def seed(db):
        db.add(models.OrgSchedule(org_id=org.org_id, notify_on_finish=channel == "mail",
                                  notify_webhook_url="https://receiver.example.invalid/events"
                                  if channel == "chat" else None))

    with_db(maintenance_settings, seed)
    with TestClient(create_app(maintenance_settings)) as client:
        notifier = client.app.state.notifier
        notifier.mailer = MailSink()
        event = _event(org.org_id)
        if entry == "listener":
            client.portal.call(notifier.on_run_finished, event)
        elif channel == "mail":
            client.portal.call(notifier._notify, event)
        else:
            client.portal.call(notifier._notify_chat, event, "https://receiver.example.invalid/events", "generic")
        client.portal.call(notifier.drain)
        assert calls == []
        assert not get_rows(maintenance_settings, models.AuditEvent,
                            models.AuditEvent.action.in_(("schedule.notified", "schedule.webhook_notified")))


@pytest.mark.parametrize("entry", ["start", "tick", "reminder"])
def test_scheduler_cannot_send_or_advance_reminders_in_maintenance(maintenance_settings, entry):
    from app.control.schedule import SchedulerLoop, send_reminder

    sent = []

    class MailSink:
        def send(self, message):
            sent.append(message)

    org = _org(maintenance_settings)
    due = utcnow()

    async def seed(db):
        db.add(models.OrgSchedule(org_id=org.org_id, reminder_enabled=True, next_reminder_at=due))

    with TestClient(create_app(maintenance_settings)) as client:
        with_db(maintenance_settings, seed)
        loop = SchedulerLoop(maintenance_settings, client.app.state.db_sessionmaker, MailSink())

        async def attempt():
            try:
                if entry == "start":
                    loop.start()
                    assert not loop.running
                elif entry == "tick":
                    assert await loop.tick() == []
                else:
                    assert await send_reminder(loop.sessionmaker, maintenance_settings, loop.mailer,
                                               org.org_id) is None
            finally:
                await loop.stop()

        client.portal.call(attempt)
        assert sent == []
        (schedule,) = get_rows(maintenance_settings, models.OrgSchedule)
        assert schedule.next_reminder_at == due


def test_startup_logs_maintenance_state(maintenance_settings, caplog):
    with caplog.at_level("INFO", logger="daas"), TestClient(create_app(maintenance_settings)):
        assert any("maintenance=true" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("route", ["magic_link", "invitation"])
def test_maintenance_blocks_transactional_mail_before_writes_or_smtp(cloud_factory, monkeypatch, enabled, route):
    import smtplib

    connections = []

    def offline_smtp(host, port, *, timeout):
        connections.append((host, port, timeout))
        raise smtplib.SMTPConnectError(421, "TEST-offline transport")

    # Keep the real SmtpMailer and route; replace only the network constructor.
    monkeypatch.setattr("app.control.mailer.smtplib.SMTP", offline_smtp)
    client, settings = cloud_factory(DAAS_MAINTENANCE_MODE=str(enabled).lower(), DAAS_EMAIL_BACKEND="smtp",
                                     DAAS_SMTP_HOST="smtp.example.invalid", DAAS_SMTP_PORT=587,
                                     DAAS_EMAIL_FROM="no-reply@example.invalid")
    org = _org(settings)
    if route == "magic_link":
        response = client.post(f"{API}/auth/magic-link", json={"email": "signin@example.invalid"})
        model = models.MagicLink
    else:
        response = client.post(f"{API}/orgs/{org.org_id}/invitations", headers=key_headers(org.api_key),
                               json={"email": "invite@example.invalid", "role": "member"})
        model = models.Invitation
    assert response.status_code == (503 if enabled else 202)
    if enabled:
        assert response.json()["code"] == "maintenance"
        assert response.headers["content-type"].startswith("application/problem+json")
        assert connections == []
        assert get_rows(settings, model) == []
    else:
        assert connections == [("smtp.example.invalid", 587, 15)]
        assert len(get_rows(settings, model)) == 1
