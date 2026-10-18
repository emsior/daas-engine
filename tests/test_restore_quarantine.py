"""Offline restore quarantine: atomic invalidation, audit counts and real credential rejection."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, text, update

from app import cli
from app.control import models
from app.control.db import upgrade_sync
from app.control.security import sha256_hex
from app.control.types import utcnow
from app.core.config import Settings
from tests.mcp_helpers import post, rpc
from tests.v1_helpers import get_rows, key_headers, seed_org, user_headers, with_db

REDIRECT = "https://connector.example.invalid/callback"
TOKEN = "TEST-restore-magic-link-012345678901234567890"


@pytest.fixture()
def restored(tmp_path):
    settings = Settings(_env_file=None, daas_control_db_url=f"sqlite+aiosqlite:///{tmp_path / 'restore.db'}")
    upgrade_sync(settings)
    org = seed_org(settings, name="TEST-Restore", owner_email="owner@example.invalid")
    other = seed_org(settings, name="TEST-Other", owner_email="other@example.invalid")
    now = utcnow()
    past, future = now - timedelta(days=1), now + timedelta(days=1)

    async def seed(db):
        db.add_all([
            models.Session(id="TEST-session", user_id=org.user_id, org_id=org.org_id,
                           csrf_token="TEST-csrf", expires_at=future),
            models.Session(id="TEST-expired-session", user_id=other.user_id,
                           csrf_token="TEST-csrf-expired", expires_at=past),
            models.MagicLink(token_hash=sha256_hex(TOKEN), email=org.email, expires_at=future),
            models.MagicLink(token_hash=sha256_hex("TEST-expired"), email=other.email, expires_at=past),
            models.MagicLink(token_hash=sha256_hex("TEST-used"), email=org.email,
                             expires_at=future, used_at=past),
            models.OAuthClient(client_id="TEST-client", info={}),
            models.OAuthClient(client_id="TEST-revoked-client", info={}, revoked_at=past),
        ])
        await db.flush()
        for status in ("pending", "approved", "exchanged", "denied", "expired"):
            db.add(models.OAuthCode(id=f"TEST-code-{status}", client_id="TEST-client",
                                   org_id=org.org_id if status != "pending" else None,
                                   code_hash=sha256_hex(f"TEST-code-{status}"),
                                   code_challenge="x" * 43, redirect_uri=REDIRECT,
                                   status=status, expires_at=future))
        db.add_all([
            models.OAuthGrant(id="TEST-grant", client_id="TEST-client", org_id=org.org_id),
            models.OAuthGrant(id="TEST-revoked-grant", client_id="TEST-client",
                              org_id=other.org_id, revoked_at=past, revocation_reason="client_revoked"),
        ])
        await db.flush()
        for kind in ("access", "refresh"):
            db.add(models.OAuthToken(id=f"TEST-{kind}", kind=kind, token_hash=sha256_hex(kind),
                                    client_id="TEST-client", grant_id="TEST-grant", org_id=org.org_id,
                                    expires_at=future))
        db.add(models.OAuthToken(id="TEST-legacy", kind="access", token_hash=sha256_hex("legacy"),
                                client_id="TEST-client", org_id=other.org_id, expires_at=past))
        db.add(models.OAuthToken(id="TEST-revoked", kind="refresh", token_hash=sha256_hex("revoked"),
                                client_id="TEST-client", grant_id="TEST-revoked-grant", org_id=other.org_id,
                                expires_at=future, revoked_at=past))
        for invitation_id, accepted_at, expires_at in (
            ("TEST-pending", None, future), ("TEST-expired", None, past), ("TEST-accepted", past, future),
        ):
            db.add(models.Invitation(id=invitation_id, org_id=org.org_id, email="invite@example.invalid",
                                     role="member", token_hash=sha256_hex(invitation_id),
                                     expires_at=expires_at, accepted_at=accepted_at))
        db.add_all([
            models.Webhook(id="TEST-hook", org_id=org.org_id, url="https://hooks.example.invalid/test",
                           secret_hash=sha256_hex("TEST-signing"), active=True),
            models.Webhook(id="TEST-disabled-hook", org_id=other.org_id,
                           url="https://hooks.example.invalid/disabled", secret_hash="0" * 64, active=False),
            models.OrgSchedule(org_id=org.org_id, reminder_enabled=True, next_reminder_at=future,
                               auto_run_on_upload=True, notify_on_finish=True,
                               notify_webhook_url="https://chat.example.invalid/TEST-secret"),
            models.OrgSchedule(org_id=other.org_id),
        ])
        existing = await db.get(models.ApiKey, other.key_id)
        existing.revoked_at = past

    with_db(settings, seed)
    return settings


def _snapshot(settings):
    async def read(db):
        return {table.name: sorted((tuple(row) for row in (await db.execute(select(table)))), key=repr)
                for table in models.Base.metadata.sorted_tables}

    return with_db(settings, read)


def _command(settings, monkeypatch, *args):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    try:
        return cli.main(["restore-quarantine", *args])
    except SystemExit as exc:
        pytest.fail(f"restore-quarantine must be available as an operator command; exit={exc.code}")


def _counts(capsys):
    captured = capsys.readouterr()
    assert captured.err == ""
    values = dict(line.split(": ", 1) for line in captured.out.strip().splitlines())
    assert all(value.isdecimal() for value in values.values()), captured.out
    return {name: int(value) for name, value in values.items()}


def test_default_quarantine_invalidates_only_replayable_credentials(restored, monkeypatch, capsys):
    before = _snapshot(restored)
    assert _command(restored, monkeypatch) == 0
    assert _counts(capsys) == {"sessions_deleted": 2, "magic_links_consumed": 2, "oauth_codes_expired": 2}
    after = _snapshot(restored)
    assert get_rows(restored, models.Session) == []
    assert all(link.used_at is not None for link in get_rows(restored, models.MagicLink))
    codes = {code.id: code.status for code in get_rows(restored, models.OAuthCode)}
    assert codes == {"TEST-code-pending": "expired", "TEST-code-approved": "expired",
                     "TEST-code-exchanged": "exchanged", "TEST-code-denied": "denied", "TEST-code-expired": "expired"}
    for table in before.keys() - {"sessions", "magic_links", "oauth_codes", "audit_events"}:
        assert after[table] == before[table], table


def test_revoke_all_resets_credentials_destinations_and_pending_invitations(restored, monkeypatch, capsys):
    before = _snapshot(restored)
    assert _command(restored, monkeypatch, "--revoke-all") == 0
    assert _counts(capsys) == {
        "sessions_deleted": 2, "magic_links_consumed": 2, "oauth_codes_expired": 2,
        "api_keys_revoked": 1, "oauth_grants_revoked": 1, "oauth_tokens_revoked": 3,
        "oauth_clients_revoked": 1, "invitations_deleted": 2, "webhooks_disabled": 1,
        "reminders_disabled": 1, "schedule_webhooks_disabled": 1,
    }
    for model in (models.ApiKey, models.OAuthGrant, models.OAuthToken, models.OAuthClient):
        assert all(row.revoked_at is not None for row in get_rows(restored, model))
    assert [row.id for row in get_rows(restored, models.Invitation)] == ["TEST-accepted"]
    assert not any(row.active for row in get_rows(restored, models.Webhook))
    schedules = get_rows(restored, models.OrgSchedule)
    assert all(not row.reminder_enabled and row.next_reminder_at is None for row in schedules)
    assert all(row.notify_webhook_url is None for row in schedules)
    configured = next(row for row in schedules if row.notify_on_finish)
    assert configured.auto_run_on_upload is True
    after = _snapshot(restored)
    for table in ("orgs", "users", "memberships"):
        assert after[table] == before[table]
    grant = next(row for row in get_rows(restored, models.OAuthGrant) if row.id == "TEST-revoked-grant")
    assert grant.revocation_reason == "client_revoked"


@pytest.mark.parametrize("flags", [[], ["--revoke-all"]])
def test_dry_run_reports_counts_without_any_database_or_audit_changes(restored, monkeypatch, capsys, flags):
    before = _snapshot(restored)
    assert _command(restored, monkeypatch, "--dry-run", *flags) == 0
    predicted = _counts(capsys)
    assert _snapshot(restored) == before
    assert _command(restored, monkeypatch, *flags) == 0
    assert _counts(capsys) == predicted


def test_audit_is_numeric_system_events_without_secrets_and_repeated_apply_is_empty(restored, monkeypatch, capsys):
    assert _command(restored, monkeypatch, "--revoke-all") == 0
    counts = _counts(capsys)
    events = get_rows(restored, models.AuditEvent)
    assert {event.action: event.meta for event in events} == {
        f"restore.{name}": {"count": count} for name, count in counts.items()
    }
    assert len(events) == 11
    assert all(event.actor_type == "system" and event.actor_id is None and event.org_id is None for event in events)
    serialized = json.dumps([{"action": e.action, "target": e.target, "meta": e.meta} for e in events])
    for forbidden in ("@", TOKEN, sha256_hex(TOKEN), "TEST-session", "TEST-secret"):
        assert forbidden not in serialized
    snapshot = _snapshot(restored)
    assert _command(restored, monkeypatch, "--revoke-all") == 0
    assert all(count == 0 for count in _counts(capsys).values())
    assert _snapshot(restored) == snapshot


@pytest.mark.parametrize("action", ["restore.magic_links_consumed", "restore.schedule_webhooks_disabled"])
def test_failed_audit_rolls_back_every_mutation_and_prints_no_sensitive_error(restored, monkeypatch, capsys, action):
    async def reject_audit(db):
        await db.execute(text(
            "CREATE TRIGGER reject_restore BEFORE INSERT ON audit_events "
            f"WHEN NEW.action = '{action}' BEGIN SELECT RAISE(ABORT, 'TEST-secret@example.invalid'); END"))

    with_db(restored, reject_audit)
    before = _snapshot(restored)
    assert _command(restored, monkeypatch, "--revoke-all") == 1
    output = capsys.readouterr()
    assert output.out == "" and "failed" in output.err.lower()
    assert "TEST-secret" not in output.err and "@" not in output.err
    assert _snapshot(restored) == before


def test_command_does_not_migrate_or_create_tables_in_an_uninitialized_database(tmp_path, monkeypatch, capsys):
    settings = Settings(_env_file=None, daas_control_db_url=f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
    assert _command(settings, monkeypatch) == 1
    output = capsys.readouterr()
    assert output.out == "" and "failed" in output.err.lower()

    async def tables(db):
        return (await db.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))).scalars().all()

    assert with_db(settings, tables) == []


def test_restored_consumed_magic_link_and_session_are_rejected_by_api(cloud_client, cloud_settings, monkeypatch):
    org = seed_org(cloud_settings, name="TEST-Restore", owner_email="owner@example.invalid")

    async def seed_link(db):
        db.add(models.MagicLink(token_hash=sha256_hex(TOKEN), email=org.email,
                               expires_at=utcnow() + timedelta(minutes=10)))

    with_db(cloud_settings, seed_link)
    # The link was consumed after the backup; restoring that backup makes it unused again.
    assert cloud_client.post("/api/v1/auth/magic-link/verify", json={"token": TOKEN, "confirm": True}).status_code == 200
    assert cloud_client.get("/api/v1/me").status_code == 200

    async def restore_link(db):
        await db.execute(update(models.MagicLink).values(used_at=None))

    with_db(cloud_settings, restore_link)
    assert _command(cloud_settings, monkeypatch) == 0
    assert cloud_client.get("/api/v1/me").status_code == 401
    rejected = cloud_client.post("/api/v1/auth/magic-link/verify", json={"token": TOKEN, "confirm": True})
    assert rejected.status_code == 401 and rejected.json()["code"] == "invalid_token"


def _approved_code(client, settings, org):
    registration = client.post("/register", json={"redirect_uris": [REDIRECT],
                                                "token_endpoint_auth_method": "none", "scope": "runs:read"})
    assert registration.status_code == 201
    client_id = registration.json()["client_id"]
    verifier = "TEST-verifier-" + "a" * 40
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    authorization = client.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "scope": "runs:read",
    }, follow_redirects=False)
    assert authorization.status_code == 302
    request_id = parse_qs(urlsplit(authorization.headers["location"]).query)["request"][0]
    consent = client.post(f"/api/v1/oauth/requests/{request_id}/decision",
                          headers=user_headers(settings, org.user_id),
                          json={"approve": True, "org_id": org.org_id})
    assert consent.status_code == 200
    code = parse_qs(urlsplit(consent.json()["redirect_to"]).query)["code"][0]
    return {"grant_type": "authorization_code", "client_id": client_id, "code": code,
            "code_verifier": verifier, "redirect_uri": REDIRECT}


def test_quarantine_rejects_real_approved_oauth_code_exchange(cloud_client, cloud_settings, monkeypatch):
    org = seed_org(cloud_settings, name="TEST-Restore", owner_email="owner@example.invalid")
    exchange = _approved_code(cloud_client, cloud_settings, org)
    assert _command(cloud_settings, monkeypatch) == 0
    rejected = cloud_client.post("/token", data=exchange)
    assert rejected.status_code == 400 and rejected.json()["error"] == "invalid_grant"
    assert get_rows(cloud_settings, models.OAuthToken) == []


def test_revoke_all_rejects_api_key_oauth_access_refresh_and_client(cloud_client, cloud_settings, monkeypatch):
    org = seed_org(cloud_settings, name="TEST-Restore", owner_email="owner@example.invalid")
    exchange = _approved_code(cloud_client, cloud_settings, org)
    tokens = cloud_client.post("/token", data=exchange)
    assert tokens.status_code == 200
    pair = tokens.json()
    assert cloud_client.get("/api/v1/me", headers=key_headers(org.api_key)).status_code == 200
    assert post(cloud_client, rpc("tools/list"), pair["access_token"]).status_code == 200
    assert _command(cloud_settings, monkeypatch, "--revoke-all") == 0
    assert cloud_client.get("/api/v1/me", headers=key_headers(org.api_key)).status_code == 401
    assert post(cloud_client, rpc("tools/list"), pair["access_token"]).status_code == 401
    refreshed = cloud_client.post("/token", data={"grant_type": "refresh_token",
                                 "client_id": exchange["client_id"], "refresh_token": pair["refresh_token"]})
    assert refreshed.status_code == 401 and refreshed.json()["error"] == "invalid_client"
