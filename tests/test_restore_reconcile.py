"""Restrictive offline reconciliation against a synthetic, checksummed pre-restore archive."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import tarfile
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import make_url, select, text

from app import cli
from app.control import models
from app.control.db import create_engine, make_sessionmaker, resolve_db_url, upgrade_sync
from app.control.executor import RunFinished
from app.control.ids import new_ulid
from app.control.security import sha256_hex
from app.control.tenancy import TenantPaths
from app.control.types import utcnow
from app.control.webhooks import DeliveryResult, WebhookDispatcher
from app.storage.duckdb_client import DuckDBClient
from tests.mcp_helpers import post, rpc
from tests.test_restore_quarantine import _approved_code
from tests.v1_helpers import (
    get_rows,
    key_headers,
    login_via_magic_link,
    seed_member,
    seed_org,
    user_headers,
    with_db,
)


def archive_pre(settings, tmp_path, change=lambda db: None):
    source = Path(make_url(resolve_db_url(settings)).database)
    pre = tmp_path / f"{new_ulid()}.db"
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(pre)) as dst:
        src.backup(dst)
        dst.execute("PRAGMA foreign_keys=ON")
        change(dst)
        dst.commit()
    archive = tmp_path / f"{new_ulid()}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(pre, arcname="control/control.db")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    Path(str(archive) + ".sha256").write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    return archive


def command(settings, monkeypatch, archive, *, apply=True, resolve_owner=()):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    try:
        return cli.main(["restore-reconcile", "--pre", str(archive), *(["--apply"] if apply else []),
                         *[arg for choice in resolve_owner for arg in ("--resolve-owner", choice)]])
    except SystemExit as exc:
        pytest.fail(f"restore-reconcile must be available; parser exit={exc.code}")


def snapshot(settings):
    async def read(db):
        return {table.name: sorted(map(repr, (await db.execute(select(table))).all()))
                for table in models.Base.metadata.sorted_tables}
    return with_db(settings, read)


@pytest.fixture()
def restored(cloud_settings):
    upgrade_sync(cloud_settings)
    org = seed_org(cloud_settings, name="TEST-Restore", owner_email="owner@example.invalid")
    admin = seed_member(cloud_settings, org.org_id, "admin@example.invalid", "admin")
    second_owner = seed_member(cloud_settings, org.org_id, "second@example.invalid", "owner")
    ids = {name: new_ulid() for name in ("client", "grant", "token", "hook", "invite", "upload", "run", "job")}
    future = utcnow() + timedelta(days=1)
    paths = TenantPaths.for_org(cloud_settings, org.org_id).ensure()
    (paths.uploads / f"{ids['upload']}.csv").write_text("TEST-data", encoding="utf-8")
    for fmt in ("md", "json", "html"):
        (paths.reports / f"{ids['run']}.{fmt}").write_text("TEST-report", encoding="utf-8")
    tenant = DuckDBClient(paths.duckdb)
    tenant.init_schema()
    with tenant.connect() as db:
        db.execute("INSERT INTO runs (run_id,pipeline,run_status,data_status,started_at,report_path,"
                   "report_json_path,report_html_path) VALUES (?, 'cs2_demo','SUCCESS','MOCK',?,?,?,?)",
                   [ids["run"], utcnow().replace(tzinfo=None),
                    *[str(paths.reports / f"{ids['run']}.{fmt}") for fmt in ("md", "json", "html")]])

    async def seed(db):
        db.add(models.OAuthClient(client_id=ids["client"], info={}))
        await db.flush()
        db.add(models.OAuthGrant(id=ids["grant"], client_id=ids["client"], org_id=org.org_id))
        db.add(models.Upload(id=ids["upload"], org_id=org.org_id, kind="csv", filename="TEST-data.csv",
                             bytes=9, sha256="0" * 64, path=f"{ids['upload']}.csv", retention_until=future))
        await db.flush()
        db.add_all([
            models.OAuthToken(id=ids["token"], kind="access", token_hash=sha256_hex("TEST-access"),
                              client_id=ids["client"], grant_id=ids["grant"], org_id=org.org_id, expires_at=future),
            models.Webhook(id=ids["hook"], org_id=org.org_id, url="https://hooks.example.invalid/TEST-old",
                           events=["run.finished"], secret_hash="0" * 64),
            models.Invitation(id=ids["invite"], org_id=org.org_id, email="invite@example.invalid", role="member",
                              token_hash="0" * 64, expires_at=future),
            models.OrgSchedule(org_id=org.org_id, reminder_enabled=True, next_reminder_at=future,
                               notify_on_finish=True, auto_run_on_upload=True,
                               notify_webhook_url="https://chat.example.invalid/TEST-old"),
            models.RunRecord(run_id=ids["run"], org_id=org.org_id, pipeline="cs2_demo", status="SUCCESS"),
            models.Job(id=ids["job"], org_id=org.org_id, pipeline="cs2_demo", upload_id=ids["upload"]),
        ])
    with_db(cloud_settings, seed)
    return cloud_settings, org, admin, second_owner, ids, paths


def test_dry_run_is_read_only_and_report_does_not_expose_values(restored, tmp_path, monkeypatch, capsys):
    settings, org, admin, _, ids, paths = restored
    def change(db):
        db.execute("UPDATE memberships SET role='member' WHERE user_id=?", (admin,))
        db.execute("UPDATE users SET email='changed@example.invalid' WHERE id=?", (admin,))
        db.execute("DELETE FROM uploads")
        db.execute("DELETE FROM webhooks")
    archive = archive_pre(settings, tmp_path, change)
    before = snapshot(settings)
    files = {p: p.read_bytes() for p in paths.root.rglob("*") if p.is_file()}
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert command(settings, monkeypatch, archive, apply=False) == 0
    output = capsys.readouterr().out
    assert admin in output and ids["upload"] in output and "member_demoted: 1" in output
    assert "@" not in output and "https://" not in output and "TEST-old" not in output
    assert snapshot(settings) == before
    assert all(path.read_bytes() == content for path, content in files.items())
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == archive_hash


def test_members_are_only_removed_or_demoted_and_emails_updated(restored, tmp_path, monkeypatch):
    settings, org, admin, second, _, _ = restored
    def change(db):
        db.execute("UPDATE memberships SET role='member' WHERE user_id=?", (admin,))
        db.execute("DELETE FROM memberships WHERE user_id=?", (second,))
        db.execute("UPDATE users SET email='changed@example.invalid' WHERE id=?", (admin,))
    assert command(settings, monkeypatch, archive_pre(settings, tmp_path, change)) == 0
    members = {m.user_id: m.role for m in get_rows(settings, models.Membership)}
    assert members == {org.user_id: "owner", admin: "member"}
    assert next(u for u in get_rows(settings, models.User) if u.id == admin).email == "changed@example.invalid"


@pytest.mark.parametrize("mode", ["no_owner", "owner_not_restored", "owner_only_in_pre"])
def test_owner_conflict_aborts_all_organizations_and_files(restored, tmp_path, monkeypatch, capsys, mode):
    settings, org, admin, second, _, paths = restored
    other = seed_org(settings, name="TEST-Other", owner_email="other@example.invalid")
    def change(db):
        db.execute("DELETE FROM orgs WHERE id=?", (other.org_id,))
        db.execute("DELETE FROM uploads")
        db.execute("UPDATE memberships SET role='member' WHERE org_id=?", (org.org_id,))
        if mode == "owner_not_restored":
            db.execute("UPDATE memberships SET role='owner' WHERE user_id=?", (admin,))
        if mode == "owner_only_in_pre":
            added = new_ulid()
            db.execute("INSERT INTO users (id,email,locale,created_at) VALUES (?,?,'pl',?)",
                       (added, "new@example.invalid", utcnow().isoformat()))
            db.execute("INSERT INTO memberships VALUES (?,?,'owner',?)", (org.org_id, added, utcnow().isoformat()))
    archive = archive_pre(settings, tmp_path, change)
    before = snapshot(settings)
    assert command(settings, monkeypatch, archive) != 0
    assert snapshot(settings) == before
    assert list(paths.uploads.iterdir()) and list(paths.reports.iterdir())
    output = capsys.readouterr()
    assert org.org_id in output.out and "@" not in output.out + output.err


@pytest.mark.parametrize("table,field,model", [
    ("api_keys", "id", models.ApiKey), ("oauth_grants", "id", models.OAuthGrant),
    ("oauth_tokens", "id", models.OAuthToken), ("oauth_clients", "client_id", models.OAuthClient),
])
@pytest.mark.parametrize("missing", [False, True])
def test_credentials_missing_or_revoked_in_pre_stay_revoked(restored, tmp_path, monkeypatch, table, field, model, missing):
    settings, _, _, _, _, _ = restored
    def change(db):
        db.execute(f"DELETE FROM {table}" if missing else f"UPDATE {table} SET revoked_at='2026-01-01 00:00:00'")  # noqa: S608
    assert command(settings, monkeypatch, archive_pre(settings, tmp_path, change)) == 0
    assert all(row.revoked_at is not None for row in get_rows(settings, model))


@pytest.mark.parametrize("state", ["missing", "accepted", "expired"])
def test_nonpending_invitation_removed(restored, tmp_path, monkeypatch, state):
    settings, *_ = restored
    def change(db):
        db.execute({"missing": "DELETE FROM invitations", "accepted": "UPDATE invitations SET accepted_at='2026-01-01'",
                    "expired": "UPDATE invitations SET expires_at='2026-01-01'"}[state])
    assert command(settings, monkeypatch, archive_pre(settings, tmp_path, change)) == 0
    assert get_rows(settings, models.Invitation) == []


@pytest.mark.parametrize("missing", [False, True])
def test_outbound_configuration_only_tightens(restored, tmp_path, monkeypatch, missing):
    settings, *_ = restored
    def change(db):
        if missing:
            db.execute("DELETE FROM webhooks")
            db.execute("DELETE FROM org_schedules")
        else:
            db.execute("UPDATE webhooks SET active=0,url='https://hooks.example.invalid/TEST-new',events='[]'")
            db.execute("UPDATE org_schedules SET reminder_enabled=0,notify_on_finish=0,auto_run_on_upload=0,"
                       "notify_webhook_url=NULL")
    assert command(settings, monkeypatch, archive_pre(settings, tmp_path, change)) == 0
    hooks, schedules = get_rows(settings, models.Webhook), get_rows(settings, models.OrgSchedule)
    if missing:
        assert hooks == schedules == []
    else:
        assert not hooks[0].active and hooks[0].url.endswith("TEST-new") and hooks[0].events == []
        assert not schedules[0].reminder_enabled and schedules[0].next_reminder_at is None
        assert not schedules[0].notify_on_finish and not schedules[0].auto_run_on_upload
        assert schedules[0].notify_webhook_url is None


@pytest.mark.parametrize("target", ["org", "user", "upload", "run", "report"])
def test_deleted_objects_and_runtime_data_are_removed(restored, tmp_path, monkeypatch, target):
    settings, org, admin, _, ids, paths = restored
    if target == "org":
        survivor = seed_org(settings, name="TEST-Survivor", owner_email="survivor@example.invalid")
    def change(db):
        if target == "user":
            db.execute("DELETE FROM users WHERE id=?", (admin,))
        elif target == "org":
            db.execute("DELETE FROM orgs WHERE id=?", (org.org_id,))
        else:
            db.execute({"upload": "DELETE FROM uploads", "run": "DELETE FROM run_records",
                        "report": "UPDATE run_records SET report_purged_at='2026-01-01'"}[target])
    assert command(settings, monkeypatch, archive_pre(settings, tmp_path, change)) == 0
    if target == "org":
        assert [row.id for row in get_rows(settings, models.Org)] == [survivor.org_id] and not paths.root.exists()
    elif target == "user":
        assert admin not in {u.id for u in get_rows(settings, models.User)}
    elif target == "upload":
        assert get_rows(settings, models.Upload) == [] and not list(paths.uploads.iterdir())
    else:
        assert not list(paths.reports.iterdir())
        rows = get_rows(settings, models.RunRecord)
        assert rows == [] if target == "run" else rows[0].report_purged_at is not None
        with DuckDBClient(paths.duckdb).connect() as db:
            assert db.execute("SELECT count(*) FROM runs WHERE run_id=?", [ids["run"]]).fetchone()[0] == 0


@pytest.mark.parametrize("status", ["missing", "succeeded", "failed", "cancelled"])
def test_old_queued_job_is_cancelled_without_running(restored, tmp_path, monkeypatch, status):
    settings, *_ = restored
    def change(db):
        db.execute("DELETE FROM jobs" if status == "missing" else "UPDATE jobs SET status=?",
                   () if status == "missing" else (status,))
    assert command(settings, monkeypatch, archive_pre(settings, tmp_path, change)) == 0
    job = get_rows(settings, models.Job)[0]
    assert job.status == "cancelled" and job.finished_at is not None and job.started_at is None


def test_new_objects_higher_roles_and_benign_differences_are_not_imported(restored, tmp_path, monkeypatch, capsys):
    settings, org, admin, _, _, _ = restored
    added = new_ulid()
    def change(db):
        db.execute("UPDATE memberships SET role='owner' WHERE user_id=?", (admin,))
        db.execute("UPDATE orgs SET name='TEST-New',plan='pro',retention_days=1")
        db.execute("INSERT INTO users (id,email,locale,created_at) VALUES (?,?,'pl',?)",
                   (added, "new@example.invalid", utcnow().isoformat()))
        db.execute("INSERT INTO memberships VALUES (?,?,'member',?)", (org.org_id, added, utcnow().isoformat()))
    archive = archive_pre(settings, tmp_path, change)
    assert command(settings, monkeypatch, archive) == 0
    assert next(m.role for m in get_rows(settings, models.Membership) if m.user_id == admin) == "admin"
    row = get_rows(settings, models.Org)[0]
    assert row.name == "TEST-Restore" and row.plan == "free" and row.retention_days != 1
    assert added not in {user.id for user in get_rows(settings, models.User)}
    assert "org_differences: 1" in capsys.readouterr().out


@pytest.mark.parametrize("damage", ["checksum", "missing_column", "extra_column", "corrupt", "invalid_boolean",
                                    "invalid_status"])
def test_archive_verification_and_forward_schema_compatibility(restored, tmp_path, monkeypatch, damage):
    settings, *_ = restored
    def change(db):
        if damage == "missing_column":
            db.execute("ALTER TABLE memberships RENAME COLUMN role TO future_role")
        if damage == "extra_column":
            db.execute("ALTER TABLE memberships ADD COLUMN future_column TEXT")
        if damage == "invalid_boolean":
            db.execute("UPDATE org_schedules SET notify_on_finish=2")
        if damage == "invalid_status":
            db.execute("UPDATE jobs SET status='future_terminal'")
    archive = archive_pre(settings, tmp_path, change)
    if damage == "checksum":
        Path(str(archive) + ".sha256").write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")
    if damage == "corrupt":
        archive.write_bytes(b"TEST-invalid")
        Path(str(archive) + ".sha256").write_text(
            f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
    before = snapshot(settings)
    assert command(settings, monkeypatch, archive) == (0 if damage == "extra_column" else 1)
    assert snapshot(settings) == before


def test_audit_failure_rolls_back_every_action_and_leaves_files(restored, tmp_path, monkeypatch, capsys):
    settings, _, _, _, _, paths = restored
    archive = archive_pre(settings, tmp_path, lambda db: db.execute("DELETE FROM uploads"))
    async def reject(db):
        await db.execute(text("CREATE TRIGGER reject_reconcile BEFORE INSERT ON audit_events "
                              "BEGIN SELECT RAISE(ABORT,'TEST-private@example.invalid'); END"))
    with_db(settings, reject)
    before = snapshot(settings)
    assert command(settings, monkeypatch, archive) == 1
    assert snapshot(settings) == before and list(paths.uploads.iterdir())
    assert "@" not in capsys.readouterr().err


def test_all_actions_audited_without_sensitive_values_and_apply_is_idempotent(restored, tmp_path, monkeypatch):
    settings, org, admin, second, ids, _ = restored
    def change(db):
        db.execute("UPDATE memberships SET role='member' WHERE user_id=?", (admin,))
        db.execute("DELETE FROM memberships WHERE user_id=?", (second,))
        db.execute("UPDATE users SET email='changed@example.invalid' WHERE id=?", (admin,))
        db.execute("DELETE FROM uploads")
        db.execute("DELETE FROM run_records")
        db.execute("DELETE FROM webhooks")
        db.execute("DELETE FROM invitations")
        db.execute("DELETE FROM jobs")
        db.execute("UPDATE api_keys SET revoked_at='2026-01-01'")
        db.execute("UPDATE oauth_clients SET revoked_at='2026-01-01'")
        db.execute("UPDATE org_schedules SET notify_on_finish=0,auto_run_on_upload=0")
    archive = archive_pre(settings, tmp_path, change)
    assert command(settings, monkeypatch, archive) == 0
    events = get_rows(settings, models.AuditEvent)
    actions = {e.action for e in events}
    expected = {"member_demoted", "member_removed", "email_updated", "upload_deleted", "run_deleted",
                "webhook_deleted", "invitation_deleted", "job_cancelled", "key_revoked", "client_revoked",
                "schedule_updated"}
    assert {f"restore.reconcile.{action}" for action in expected} <= actions
    assert all(e.actor_type == "system" and e.actor_id is None and len(e.action) <= 40 for e in events)
    serialized = json.dumps([{"target": e.target, "meta": e.meta} for e in events])
    assert "@" not in serialized and "https://" not in serialized and "TEST-old" not in serialized
    before = snapshot(settings)
    assert command(settings, monkeypatch, archive) == 0
    assert snapshot(settings) == before


def test_email_and_webhook_url_swaps_are_atomic(restored, tmp_path, monkeypatch):
    settings, org, admin, _, ids, _ = restored
    second_hook = new_ulid()
    async def seed(db):
        db.add(models.Webhook(id=second_hook, org_id=org.org_id, url="https://hooks.example.invalid/TEST-second",
                             secret_hash="0" * 64, events=[]))
    with_db(settings, seed)
    def change(db):
        db.execute("UPDATE users SET email='swap@example.invalid' WHERE id=?", (admin,))
        db.execute("UPDATE users SET email='admin@example.invalid' WHERE id=?", (org.user_id,))
        db.execute("UPDATE users SET email='owner@example.invalid' WHERE id=?", (admin,))
        db.execute("UPDATE webhooks SET url='https://hooks.example.invalid/TEST-temp' WHERE id=?", (ids["hook"],))
        db.execute("UPDATE webhooks SET url='https://hooks.example.invalid/TEST-old' WHERE id=?", (second_hook,))
        db.execute("UPDATE webhooks SET url='https://hooks.example.invalid/TEST-second' WHERE id=?", (ids["hook"],))
    assert command(settings, monkeypatch, archive_pre(settings, tmp_path, change)) == 0
    users = {u.id: u.email for u in get_rows(settings, models.User)}
    hooks = {h.id: h.url for h in get_rows(settings, models.Webhook)}
    assert users[org.user_id] == "admin@example.invalid" and users[admin] == "owner@example.invalid"
    assert hooks[ids["hook"]].endswith("TEST-second") and hooks[second_hook].endswith("TEST-old")


def test_cleanup_failure_is_nonzero_after_commit_and_not_reported_as_rollback(restored, tmp_path, monkeypatch, capsys):
    settings, _, _, _, ids, paths = restored
    archive = archive_pre(settings, tmp_path, lambda db: db.execute("DELETE FROM uploads"))
    original = Path.unlink
    def deny(path, *args, **kwargs):
        if path == paths.uploads / f"{ids['upload']}.csv":
            raise PermissionError("TEST denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", deny)
    assert command(settings, monkeypatch, archive) == 1
    assert get_rows(settings, models.Upload) == []
    assert (paths.uploads / f"{ids['upload']}.csv").exists()
    output = capsys.readouterr()
    assert "cleanup" in (output.out + output.err).lower()


@pytest.mark.parametrize("damage", ["link", "duplicate", "traversal", "missing_db"])
def test_unsafe_archive_does_not_change_database(restored, tmp_path, monkeypatch, damage):
    import io
    settings, *_ = restored
    archive = archive_pre(settings, tmp_path)
    with tarfile.open(archive, "r:gz") as tar:
        database = tar.extractfile("control/control.db").read()
    with tarfile.open(archive, "w:gz") as tar:
        if damage != "missing_db":
            member = tarfile.TarInfo("control/control.db")
            if damage == "link":
                member.type = tarfile.SYMTYPE
                member.linkname = "TEST-outside"
                tar.addfile(member)
            else:
                member.size = len(database)
                tar.addfile(member, io.BytesIO(database))
                if damage == "duplicate":
                    tar.addfile(member, io.BytesIO(database))
        if damage == "traversal":
            member = tarfile.TarInfo("control/../TEST-outside")
            tar.addfile(member, io.BytesIO())
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    Path(str(archive) + ".sha256").write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    before = snapshot(settings)
    assert command(settings, monkeypatch, archive) == 1
    assert snapshot(settings) == before


def test_reconciled_admin_cannot_manage_members_with_existing_or_new_session(
        cloud_client, cloud_settings, tmp_path, monkeypatch):
    org = seed_org(cloud_settings, name="TEST-Auth", owner_email="owner@example.invalid")
    admin = seed_member(cloud_settings, org.org_id, "admin@example.invalid", "admin")
    headers = user_headers(cloud_settings, admin)
    url = f"/api/v1/orgs/{org.org_id}/invitations"
    assert cloud_client.get(url, headers=headers).status_code == 200
    archive = archive_pre(cloud_settings, tmp_path,
                          lambda db: db.execute("UPDATE memberships SET role='member' WHERE user_id=?", (admin,)))
    assert command(cloud_settings, monkeypatch, archive) == 0
    for auth in (headers, user_headers(cloud_settings, admin)):
        assert cloud_client.get(url, headers=auth).status_code == 403


def test_reconciled_admin_stays_demoted_after_real_login(cloud_client, cloud_settings, tmp_path, monkeypatch):
    org = seed_org(cloud_settings, name="TEST-Login", owner_email="owner@example.invalid")
    admin = seed_member(cloud_settings, org.org_id, "admin@example.invalid", "admin")
    archive = archive_pre(cloud_settings, tmp_path,
                          lambda db: db.execute("UPDATE memberships SET role='member' WHERE user_id=?", (admin,)))
    assert command(cloud_settings, monkeypatch, archive) == 0
    login_via_magic_link(cloud_client, cloud_settings, "admin@example.invalid")
    response = cloud_client.get(f"/api/v1/orgs/{org.org_id}/invitations")
    assert response.status_code == 403


def test_revoked_key_and_oauth_grant_are_rejected_by_real_endpoints(cloud_client, cloud_settings, tmp_path, monkeypatch):
    org = seed_org(cloud_settings, name="TEST-Credentials", owner_email="owner@example.invalid")
    exchange = _approved_code(cloud_client, cloud_settings, org)
    pair = cloud_client.post("/token", data=exchange).json()
    assert post(cloud_client, rpc("tools/list"), pair["access_token"]).status_code == 200
    def change(db):
        db.execute("UPDATE api_keys SET revoked_at='2026-01-01'")
        db.execute("UPDATE oauth_grants SET revoked_at='2026-01-01'")
    assert command(cloud_settings, monkeypatch, archive_pre(cloud_settings, tmp_path, change)) == 0
    assert cloud_client.get("/api/v1/me", headers=key_headers(org.api_key)).status_code == 401
    assert post(cloud_client, rpc("tools/list"), pair["access_token"]).status_code == 401


def test_cleanup_does_not_initialize_schema_or_purge_unrelated_old_runs(restored, tmp_path, monkeypatch):
    settings, org, _, _, ids, paths = restored
    keep = new_ulid()
    with DuckDBClient(paths.duckdb).connect() as db:
        db.execute("INSERT INTO runs (run_id,pipeline,run_status,data_status,started_at) "
                   "VALUES (?,'cs2_demo','SUCCESS','MOCK','2001-01-01')", [keep])
    async def seed(db):
        db.add(models.RunRecord(run_id=keep, org_id=org.org_id, pipeline="cs2_demo", status="SUCCESS"))
    with_db(settings, seed)
    archive = archive_pre(settings, tmp_path,
                          lambda db: db.execute("DELETE FROM run_records WHERE run_id=?", (ids["run"],)))
    def no_schema(*args, **kwargs):
        pytest.fail("offline reconciliation must not initialize or migrate tenant schema")
    monkeypatch.setattr(DuckDBClient, "init_schema", no_schema)
    assert command(settings, monkeypatch, archive) == 0
    import duckdb
    with duckdb.connect(str(paths.duckdb)) as db:
        assert db.execute("SELECT run_id FROM runs").fetchall() == [(keep,)]


def test_deleted_webhook_is_not_selected_for_delivery(restored, tmp_path, monkeypatch):
    settings, org, _, _, ids, _ = restored
    delivered = []
    async def observe(self, hook, *args, **kwargs):
        delivered.append(hook.id)
        return DeliveryResult(True, 200, 1, new_ulid())
    monkeypatch.setattr(WebhookDispatcher, "deliver", observe)
    async def dispatch():
        engine = create_engine(settings)
        dispatcher = WebhookDispatcher(settings, make_sessionmaker(engine))
        try:
            await dispatcher.on_run_finished(RunFinished(org.org_id, ids["job"], ids["run"], "cs2_demo",
                                                        "succeeded", "SUCCESS", utcnow().isoformat()))
            await dispatcher.drain()
        finally:
            await dispatcher.close()
            await engine.dispose()
    asyncio.run(dispatch())
    assert delivered == [ids["hook"]]
    delivered.clear()
    archive = archive_pre(settings, tmp_path, lambda db: db.execute("DELETE FROM webhooks"))
    assert command(settings, monkeypatch, archive) == 0
    asyncio.run(dispatch())
    assert delivered == []


def _redirect_runtime_path(path, target, tmp_path, *, directory):
    import os
    import shutil
    import subprocess

    original = path.with_name(f"{path.name}-original")
    boundary = tmp_path.resolve()
    assert path.resolve().is_relative_to(boundary)
    assert target.resolve().is_relative_to(boundary)
    assert original.resolve().is_relative_to(boundary)
    path.rename(original)
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError:
        if os.name != "nt" or not directory:
            pytest.skip("this filesystem does not permit the required symbolic link")
        executable = shutil.which("cmd")
        if executable is None:
            pytest.skip("the Windows command processor is unavailable for directory junctions")
        # Only the test-created paths checked against tmp_path above reach mklink.
        result = subprocess.run(  # noqa: S603
            [executable, "/c", "mklink", "/J", str(path), str(target)], capture_output=True, check=False,
        )
        if result.returncode:
            pytest.skip("this filesystem does not permit directory junctions")


@pytest.mark.parametrize("redirect", ["root", "uploads", "reports", "duckdb"])
def test_redirected_runtime_path_aborts_before_control_or_sibling_data_changes(
        restored, tmp_path, monkeypatch, redirect):
    settings, org, _, _, ids, paths = restored
    other = seed_org(settings, name="TEST-Sibling", owner_email="sibling@example.invalid")
    other_paths = TenantPaths.for_org(settings, other.org_id).ensure()
    (other_paths.uploads / f"{ids['upload']}.csv").write_text("TEST-sibling-upload", encoding="utf-8")
    for fmt in ("md", "json", "html"):
        (other_paths.reports / f"{ids['run']}.{fmt}").write_text("TEST-sibling-report", encoding="utf-8")
    tenant = DuckDBClient(other_paths.duckdb)
    with tenant.connect() as db:
        db.execute("INSERT INTO runs (run_id,pipeline,run_status,data_status,started_at) "
                   "VALUES (?,'cs2_demo','SUCCESS','MOCK','2026-01-01')", [ids["run"]])

    def change(db):
        if redirect == "root":
            db.execute("DELETE FROM orgs WHERE id=?", (org.org_id,))
        elif redirect == "uploads":
            db.execute("DELETE FROM uploads WHERE org_id=?", (org.org_id,))
        else:
            db.execute("DELETE FROM run_records WHERE org_id=?", (org.org_id,))

    archive = archive_pre(settings, tmp_path, change)
    source = getattr(paths, redirect)
    target = getattr(other_paths, redirect)
    _redirect_runtime_path(source, target, tmp_path, directory=redirect != "duckdb")
    before = snapshot(settings)
    sibling_files = {p.relative_to(other_paths.root): p.read_bytes()
                     for p in other_paths.root.rglob("*") if p.is_file()}
    result = command(settings, monkeypatch, archive)
    unchanged_control = snapshot(settings) == before
    unchanged_sibling = all((other_paths.root / name).is_file()
                            and (other_paths.root / name).read_bytes() == contents
                            for name, contents in sibling_files.items())
    assert unchanged_control and unchanged_sibling, "redirected runtime paths must cause no writes or deletion"
    assert result != 0


def _runtime_snapshot(paths):
    return {path.relative_to(paths.root): path.read_bytes() for path in paths.root.rglob("*") if path.is_file()}


def _transfer_owner(db, org_id, owner_id):
    db.execute("DELETE FROM users WHERE id IN (SELECT user_id FROM memberships WHERE org_id=? AND role='owner')",
               (org_id,))
    db.execute("UPDATE memberships SET role='owner' WHERE org_id=? AND user_id=?", (org_id, owner_id))
    db.execute("DELETE FROM uploads WHERE org_id=?", (org_id,))


@pytest.mark.parametrize("apply", [False, True], ids=["dry_run", "apply"])
@pytest.mark.parametrize("history", ["empty", "unrelated"])
def test_pre_without_shared_history_rejected_without_changes(restored, tmp_path, monkeypatch, capsys, apply, history):
    settings, org, _, _, _, paths = restored
    unrelated = new_ulid()
    def change(db):
        for table in reversed(models.Base.metadata.sorted_tables):
            db.execute(f'DELETE FROM "{table.name}"')  # noqa: S608
        if history == "unrelated":
            db.execute("INSERT INTO orgs (id,slug,name,plan,retention_days,locale,llm_narrative,created_at) "
                       "VALUES (?,'test-unrelated','TEST-Unrelated','free',30,'pl',0,?)", (unrelated, utcnow().isoformat()))
    archive = archive_pre(settings, tmp_path, change)
    before, files = snapshot(settings), _runtime_snapshot(paths)
    result = command(settings, monkeypatch, archive, apply=apply)
    output = capsys.readouterr()
    assert result != 0, "an unrelated pre-restore archive must be rejected in both modes"
    assert snapshot(settings) == before and _runtime_snapshot(paths) == files
    assert "5.4" in output.err and "history" in output.err.lower()
    assert org.org_id not in output.err and unrelated not in output.err and "@" not in output.err


def test_owner_transfer_conflict_dry_run_lists_eligible_pre_owner(restored, tmp_path, monkeypatch, capsys):
    settings, org, admin, _, _, paths = restored
    archive = archive_pre(settings, tmp_path, lambda db: _transfer_owner(db, org.org_id, admin))
    before, files = snapshot(settings), _runtime_snapshot(paths)
    assert command(settings, monkeypatch, archive, apply=False) == 0
    output = capsys.readouterr()
    assert org.org_id in output.out and admin in output.out
    assert "@" not in output.out + output.err
    assert snapshot(settings) == before and _runtime_snapshot(paths) == files


def test_owner_transfer_without_resolution_is_atomic(restored, tmp_path, monkeypatch):
    settings, org, admin, _, _, paths = restored
    archive = archive_pre(settings, tmp_path, lambda db: _transfer_owner(db, org.org_id, admin))
    before, files = snapshot(settings), _runtime_snapshot(paths)
    assert command(settings, monkeypatch, archive) != 0
    assert snapshot(settings) == before and _runtime_snapshot(paths) == files


def test_explicit_owner_resolution_applies_with_deletions_and_safe_audit(restored, tmp_path, monkeypatch):
    settings, org, admin, second, _, paths = restored
    archive = archive_pre(settings, tmp_path, lambda db: _transfer_owner(db, org.org_id, admin))
    assert command(settings, monkeypatch, archive, resolve_owner=[f"{org.org_id}:{admin}"]) == 0
    assert {(row.org_id, row.user_id): row.role for row in get_rows(settings, models.Membership)} == {
        (org.org_id, admin): "owner"}
    assert not {org.user_id, second} & {row.id for row in get_rows(settings, models.User)}
    assert get_rows(settings, models.Upload) == [] and not list(paths.uploads.iterdir())
    events = get_rows(settings, models.AuditEvent)
    resolutions = [event for event in events if event.action == "restore.reconcile.owner_resolved"]
    assert len(resolutions) == 1 and resolutions[0].target == f"{org.org_id}:{admin}"
    assert resolutions[0].actor_type == "system" and resolutions[0].actor_id is None
    assert "@" not in json.dumps([{"target": event.target, "meta": event.meta} for event in events])


@pytest.mark.parametrize("invalid", ["no_conflict", "not_pre_owner", "no_restored_membership", "deleted_account"])
def test_ineligible_owner_resolution_changes_nothing(restored, tmp_path, monkeypatch, capsys, invalid):
    settings, org, admin, _, _, paths = restored
    candidate = seed_member(settings, org.org_id, "candidate@example.invalid", "member")
    if invalid == "no_restored_membership":
        other = seed_org(settings, name="TEST-Other", owner_email="other@example.invalid")
        candidate = other.user_id
    def change(db):
        if invalid != "no_conflict":
            _transfer_owner(db, org.org_id, admin)
        if invalid == "no_conflict":
            db.execute("UPDATE memberships SET role='owner' WHERE user_id=?", (candidate,))
        elif invalid == "no_restored_membership":
            db.execute("INSERT INTO memberships VALUES (?,?,'owner',?)", (org.org_id, candidate, utcnow().isoformat()))
        elif invalid == "deleted_account":
            db.execute("DELETE FROM users WHERE id=?", (candidate,))
        db.execute("DELETE FROM uploads")
    archive = archive_pre(settings, tmp_path, change)
    before, files = snapshot(settings), _runtime_snapshot(paths)
    assert command(settings, monkeypatch, archive, resolve_owner=[f"{org.org_id}:{candidate}"]) != 0
    assert snapshot(settings) == before and _runtime_snapshot(paths) == files
    assert "@" not in capsys.readouterr().err


@pytest.mark.parametrize("selection", ["missing_separator", "empty_org", "empty_user", "invalid_org", "invalid_user",
                                       "overflow_org", "extra_separator"])
def test_invalid_owner_resolution_identifier_changes_nothing(restored, tmp_path, monkeypatch, capsys, selection):
    settings, org, admin, _, _, paths = restored
    archive = archive_pre(settings, tmp_path, lambda db: _transfer_owner(db, org.org_id, admin))
    choices = {"missing_separator": org.org_id, "empty_org": f":{admin}", "empty_user": f"{org.org_id}:",
               "invalid_org": f"TEST-invalid:{admin}", "invalid_user": f"{org.org_id}:invalid@example.invalid",
               "overflow_org": f"8{org.org_id[1:]}:{admin}", "extra_separator": f"{org.org_id}:{admin}:extra"}
    before, files = snapshot(settings), _runtime_snapshot(paths)
    assert command(settings, monkeypatch, archive, resolve_owner=[choices[selection]]) != 0
    assert snapshot(settings) == before and _runtime_snapshot(paths) == files
    assert "@" not in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["dry_run", "duplicate"])
def test_owner_resolution_requires_apply_and_unique_organization(restored, tmp_path, monkeypatch, mode):
    settings, org, admin, _, _, paths = restored
    archive = archive_pre(settings, tmp_path, lambda db: _transfer_owner(db, org.org_id, admin))
    before, files = snapshot(settings), _runtime_snapshot(paths)
    choices = [f"{org.org_id}:{admin}"] * (2 if mode == "duplicate" else 1)
    assert command(settings, monkeypatch, archive, apply=mode != "dry_run", resolve_owner=choices) != 0
    assert snapshot(settings) == before and _runtime_snapshot(paths) == files


@pytest.mark.parametrize("resolve_all", [False, True])
def test_multiple_owner_conflicts_are_resolved_atomically(restored, tmp_path, monkeypatch, resolve_all):
    settings, org, admin, _, _, paths = restored
    other = seed_org(settings, name="TEST-Other", owner_email="other@example.invalid")
    other_member = seed_member(settings, other.org_id, "other-member@example.invalid", "member")
    def change(db):
        _transfer_owner(db, org.org_id, admin)
        _transfer_owner(db, other.org_id, other_member)
    archive = archive_pre(settings, tmp_path, change)
    before, files = snapshot(settings), _runtime_snapshot(paths)
    choices = [f"{org.org_id}:{admin}"]
    if resolve_all:
        choices.append(f"{other.org_id}:{other_member}")
    result = command(settings, monkeypatch, archive, resolve_owner=choices)
    if resolve_all:
        assert result == 0
        assert {(row.org_id, row.user_id): row.role for row in get_rows(settings, models.Membership)} == {
            (org.org_id, admin): "owner", (other.org_id, other_member): "owner"}
        events = get_rows(settings, models.AuditEvent)
        assert {event.target for event in events if event.action == "restore.reconcile.owner_resolved"} == set(choices)
    else:
        assert result != 0
        assert snapshot(settings) == before and _runtime_snapshot(paths) == files


def test_owner_resolution_audit_failure_rolls_back_promotion_and_deletions(restored, tmp_path, monkeypatch, capsys):
    settings, org, admin, _, _, paths = restored
    archive = archive_pre(settings, tmp_path, lambda db: _transfer_owner(db, org.org_id, admin))
    async def reject(db):
        await db.execute(text("CREATE TRIGGER reject_resolution BEFORE INSERT ON audit_events "
                              "WHEN NEW.action = 'restore.reconcile.owner_resolved' "
                              "BEGIN SELECT RAISE(ABORT,'TEST-private@example.invalid'); END"))
    with_db(settings, reject)
    before, files = snapshot(settings), _runtime_snapshot(paths)
    assert command(settings, monkeypatch, archive, resolve_owner=[f"{org.org_id}:{admin}"]) != 0
    assert snapshot(settings) == before and _runtime_snapshot(paths) == files
    assert "@" not in capsys.readouterr().err


@pytest.mark.parametrize("role", ["admin", "member"])
def test_resolved_owner_can_manage_members_after_real_login(cloud_client, cloud_settings, tmp_path, monkeypatch, role):
    org = seed_org(cloud_settings, name="TEST-Resolved-Login", owner_email="owner@example.invalid")
    member = seed_member(cloud_settings, org.org_id, "member@example.invalid", role)
    url = f"/api/v1/orgs/{org.org_id}/invitations"
    assert cloud_client.get(url, headers=user_headers(cloud_settings, member)).status_code == (200 if role == "admin" else 403)
    archive = archive_pre(cloud_settings, tmp_path, lambda db: _transfer_owner(db, org.org_id, member))
    assert command(cloud_settings, monkeypatch, archive, resolve_owner=[f"{org.org_id}:{member}"]) == 0
    assert [(row.user_id, row.role) for row in get_rows(cloud_settings, models.Membership)] == [(member, "owner")]
    login_via_magic_link(cloud_client, cloud_settings, "member@example.invalid")
    assert cloud_client.get(url).status_code == 200
