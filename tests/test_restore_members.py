"""Offline membership corrections keep the last owner and never disclose e-mail by default."""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import select, text

from app import cli
from app.control import models
from app.control.ids import new_ulid
from tests.v1_helpers import (
    get_rows,
    login_via_magic_link,
    seed_member,
    seed_org,
    user_headers,
    with_db,
)


@pytest.fixture()
def restored_members(cloud_settings):
    org = seed_org(cloud_settings, name="TEST-Restore", owner_email="owner@example.invalid")
    admin = seed_member(cloud_settings, org.org_id, "admin@example.invalid", "admin")
    member = seed_member(cloud_settings, org.org_id, "member@example.invalid", "member")
    return cloud_settings, org, admin, member


def _snapshot(settings):
    async def read(db):
        return {table.name: sorted((tuple(row) for row in (await db.execute(select(table)))), key=repr)
                for table in models.Base.metadata.sorted_tables}

    return with_db(settings, read)


def _command(settings, monkeypatch, *args):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    try:
        return cli.main(["restore-members", *args])
    except SystemExit as exc:
        pytest.fail(f"restore-members must be available as an operator command; exit={exc.code}")


def _roles(settings, org_id):
    return {row.user_id: row.role for row in get_rows(settings, models.Membership,
                                                     models.Membership.org_id == org_id)}


def test_list_has_ids_roles_dates_and_no_emails_or_database_writes(restored_members, monkeypatch, capsys):
    settings, org, admin, member = restored_members
    before = _snapshot(settings)
    assert _command(settings, monkeypatch) == 0
    output = capsys.readouterr()
    assert output.err == "" and "@" not in output.out
    rows = json.loads(output.out)
    assert {(row["org_id"], row["user_id"], row["role"]) for row in rows} == {
        (org.org_id, org.user_id, "owner"), (org.org_id, admin, "admin"), (org.org_id, member, "member"),
    }
    for row in rows:
        assert set(row) == {"org_id", "user_id", "role", "membership_created_at", "user_created_at"}
        assert datetime.fromisoformat(row["membership_created_at"])
        assert datetime.fromisoformat(row["user_created_at"])
    assert _snapshot(settings) == before


def test_show_emails_is_explicit_terminal_output_without_audit(restored_members, monkeypatch, capsys, caplog):
    settings, org, _admin, _member = restored_members
    before = _snapshot(settings)
    assert _command(settings, monkeypatch, "--org", org.org_id, "--show-emails") == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert {row["email"] for row in json.loads(output.out)} == {
        "owner@example.invalid", "admin@example.invalid", "member@example.invalid",
    }
    assert "@" not in caplog.text
    assert _snapshot(settings) == before


@pytest.mark.parametrize("action", [("--remove",), ("--demote-to", "member")])
def test_correction_removes_manage_access_from_existing_session(
        restored_members, cloud_client, monkeypatch, capsys, action):
    settings, org, admin, _member = restored_members
    headers = user_headers(settings, admin)
    url = f"/api/v1/orgs/{org.org_id}/invitations"
    assert cloud_client.get(url, headers=headers).status_code == 200
    assert _command(settings, monkeypatch, "--org", org.org_id, "--user", admin, *action) == 0
    assert cloud_client.get(url, headers=headers).status_code == 403
    roles = _roles(settings, org.org_id)
    assert (admin not in roles) if action == ("--remove",) else roles[admin] == "member"
    assert roles[org.user_id] == "owner"
    event, = get_rows(settings, models.AuditEvent,
                      models.AuditEvent.action.like("restore.member_%"))
    assert event.action == ("restore.member_removed" if action == ("--remove",) else "restore.member_demoted")
    assert event.org_id == org.org_id and event.target == f"user:{admin}"
    assert event.actor_type == "system" and event.actor_id is None
    assert "@" not in json.dumps(event.meta)
    assert "@" not in capsys.readouterr().out


@pytest.mark.parametrize("action", [("--remove",), ("--demote-to", "member")])
def test_two_owners_allow_offline_correction_without_an_owner_session(restored_members, monkeypatch, action):
    settings, org, _admin, _member = restored_members
    second = seed_member(settings, org.org_id, "second-owner@example.invalid", "owner")
    assert _command(settings, monkeypatch, "--org", org.org_id, "--user", second, *action) == 0
    roles = _roles(settings, org.org_id)
    assert roles[org.user_id] == "owner"
    assert (second not in roles) if action == ("--remove",) else roles[second] == "member"


@pytest.mark.parametrize("action", [("--remove",), ("--demote-to", "admin"), ("--demote-to", "member")])
def test_last_owner_is_protected_with_zero_changes(restored_members, monkeypatch, capsys, action):
    settings, org, _admin, _member = restored_members
    before = _snapshot(settings)
    assert _command(settings, monkeypatch, "--org", org.org_id, "--user", org.user_id, *action) != 0
    output = capsys.readouterr()
    assert output.out == "" and output.err and "@" not in output.err
    assert _snapshot(settings) == before


@pytest.mark.parametrize("role", ["admin", "member"])
def test_promotion_and_same_role_are_rejected_without_changes(restored_members, monkeypatch, capsys, role):
    settings, org, _admin, member = restored_members
    before = _snapshot(settings)
    assert _command(settings, monkeypatch, "--org", org.org_id, "--user", member, "--demote-to", role) != 0
    assert capsys.readouterr().out == ""
    assert _snapshot(settings) == before


@pytest.mark.parametrize("user", ["TEST-secret@example.invalid", None])
def test_unknown_or_malformed_identifier_is_safe_and_atomic(restored_members, monkeypatch, capsys, user):
    settings, org, _admin, _member = restored_members
    before = _snapshot(settings)
    assert _command(settings, monkeypatch, "--org", org.org_id, "--user", user or new_ulid(), "--remove") != 0
    output = capsys.readouterr()
    assert output.out == "" and "@" not in output.err and "TEST-secret" not in output.err
    assert _snapshot(settings) == before


def test_failed_audit_rolls_back_membership_and_redacts_database_error(restored_members, monkeypatch, capsys):
    settings, org, admin, _member = restored_members

    async def reject_audit(db):
        await db.execute(text(
            "CREATE TRIGGER reject_restore_member BEFORE INSERT ON audit_events "
            "WHEN NEW.action = 'restore.member_removed' "
            "BEGIN SELECT RAISE(ABORT, 'TEST-secret@example.invalid'); END"))

    with_db(settings, reject_audit)
    before = _snapshot(settings)
    assert _command(settings, monkeypatch, "--org", org.org_id, "--user", admin, "--remove") != 0
    output = capsys.readouterr()
    assert output.out == "" and output.err and "@" not in output.err and "TEST-secret" not in output.err
    assert _snapshot(settings) == before


def test_removed_admin_stays_out_after_real_magic_link_login(restored_members, cloud_client, monkeypatch):
    settings, org, admin, _member = restored_members
    assert _command(settings, monkeypatch, "--org", org.org_id, "--user", admin, "--remove") == 0
    body = login_via_magic_link(cloud_client, settings, "admin@example.invalid")
    assert org.org_id not in {row["org_id"] for row in body["orgs"]}
    assert cloud_client.get(f"/api/v1/orgs/{org.org_id}/invitations").status_code == 403


def test_demoted_admin_stays_restricted_after_real_magic_link_login(restored_members, cloud_client, monkeypatch):
    settings, org, admin, _member = restored_members
    assert _command(settings, monkeypatch, "--org", org.org_id, "--user", admin, "--demote-to", "member") == 0
    body = login_via_magic_link(cloud_client, settings, "admin@example.invalid")
    assert next(row for row in body["orgs"] if row["org_id"] == org.org_id)["role"] == "member"
    assert cloud_client.get(f"/api/v1/orgs/{org.org_id}/invitations").status_code == 403
