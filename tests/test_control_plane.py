"""Control plane: modele, migracje od zera, identyfikatory, klucze, CLI (`migrate`, `create-org`)."""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.cli import main as cli_main
from app.control import models, security, service
from app.control import scopes as sc
from app.control.db import create_engine, make_sessionmaker, resolve_db_url, upgrade_sync
from app.control.ids import new_ulid
from app.control.models import Base
from app.control.types import utcnow
from app.core.config import PROJECT_ROOT, Settings, reset_settings_cache

EXPECTED_TABLES = {
    "orgs", "users", "memberships", "api_keys", "magic_links", "sessions", "invitations", "jobs",
    "uploads", "run_records", "audit_events", "usage_counters", "webhooks",
}


def _settings(tmp_path: Path, name: str = "c.db") -> Settings:
    return Settings(_env_file=None, daas_control_db_url=f"sqlite+aiosqlite:///{tmp_path / name}")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def migrated(tmp_path: Path) -> Settings:
    s = _settings(tmp_path)
    upgrade_sync(s)
    return s


async def _with_session(settings: Settings, fn):
    engine = create_engine(settings)
    try:
        async with make_sessionmaker(engine)() as db:
            return await fn(db)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------- migracje
def test_migration_from_scratch_creates_all_tables(tmp_path: Path):
    s = _settings(tmp_path)
    assert not (tmp_path / "c.db").exists()
    upgrade_sync(s)

    async def tables(db):
        return await db.run_sync(lambda sync: set(inspect(sync.connection()).get_table_names()))

    names = run(_with_session(s, tables))
    assert EXPECTED_TABLES <= names
    assert "alembic_version" in names


def test_migration_matches_models_exactly(migrated: Settings):
    """Autogeneracja po migracji nie moze znalezc roznic: model i migracje do 0003 sa zgodne."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def diff(sync_conn):
        ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})
        return compare_metadata(ctx, Base.metadata)

    async def go(db):
        return await db.run_sync(lambda sync: diff(sync.connection()))

    assert run(_with_session(migrated, go)) == []


def test_migration_is_idempotent(migrated: Settings):
    upgrade_sync(migrated)
    upgrade_sync(migrated)

    async def version(db):
        return (await db.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()

    assert run(_with_session(migrated, version)) == ["0003"]


def test_migration_downgrade_removes_everything(tmp_path: Path):
    from alembic import command
    from app.control.db import alembic_config

    s = _settings(tmp_path)
    upgrade_sync(s)
    command.downgrade(alembic_config(resolve_db_url(s)), "base")

    async def tables(db):
        return await db.run_sync(lambda sync: set(inspect(sync.connection()).get_table_names()))

    assert run(_with_session(s, tables)) <= {"alembic_version"}


def test_cli_migrate_creates_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setenv("DAAS_CONTROL_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")
    reset_settings_cache()
    assert cli_main(["migrate"]) == 0
    assert (tmp_path / "cli.db").is_file()
    assert "head" in capsys.readouterr().out


def test_alembic_cli_entrypoint_uses_settings_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`alembic upgrade head` z linii polecen (CI, Docker) bierze URL z DAAS_CONTROL_DB_URL."""
    import subprocess
    import sys

    env = {"DAAS_CONTROL_DB_URL": f"sqlite+aiosqlite:///{tmp_path / 'viacli.db'}",
           "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=PROJECT_ROOT, env=env,
        capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "viacli.db").is_file()


def test_relative_sqlite_url_is_resolved_against_project_root():
    s = Settings(_env_file=None, daas_control_db_url="sqlite+aiosqlite:///runtime/control.db")
    assert resolve_db_url(s) == f"sqlite+aiosqlite:///{PROJECT_ROOT / 'runtime' / 'control.db'}"


def test_default_control_db_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DAAS_CONTROL_DB_URL")
    assert Settings(_env_file=None).daas_control_db_url == "sqlite+aiosqlite:///runtime/control.db"


# ---------------------------------------------------------------- modele
def test_every_table_has_a_model():
    assert {t.name for t in Base.metadata.sorted_tables} == EXPECTED_TABLES


def test_org_user_membership_roundtrip(migrated: Settings):
    async def go(db):
        user, created = await service.get_or_create_user(db, "  Owner@Example.TEST ")
        assert created and user.email == "owner@example.test"
        again, created2 = await service.get_or_create_user(db, "OWNER@example.test")
        assert not created2 and again.id == user.id
        org = await service.create_org(db, name="Zażółć Gęślą Jaźń Sp. z o.o.", owner=user, dpa_version="v1")
        await db.commit()
        return org.id, org.slug, org.dpa_accepted_at, org.plan, org.retention_days

    org_id, slug, accepted, plan, retention = run(_with_session(migrated, go))
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", org_id)
    assert slug == "zazolc-gesla-jazn-sp-z-o-o"
    assert accepted is not None and accepted.tzinfo is not None
    assert plan == "free" and retention == 90


def test_slug_is_unique_for_same_name(migrated: Settings):
    async def go(db):
        a = await service.create_org(db, name="Acme", owner=None)
        b = await service.create_org(db, name="Acme", owner=None)
        return a.slug, b.slug

    assert run(_with_session(migrated, go)) == ("acme", "acme-2")


def test_email_is_unique(migrated: Settings):
    async def go(db):
        db.add(models.User(email="a@example.test"))
        db.add(models.User(email="a@example.test"))
        await db.commit()

    with pytest.raises(IntegrityError):
        run(_with_session(migrated, go))


def test_foreign_keys_are_enforced(migrated: Settings):
    async def go(db):
        db.add(models.Membership(org_id="nope", user_id="nope", role="owner"))
        await db.commit()

    with pytest.raises(IntegrityError):
        run(_with_session(migrated, go))


def test_deleting_org_cascades(migrated: Settings):
    async def go(db):
        user, _ = await service.get_or_create_user(db, "o@example.test")
        org = await service.create_org(db, name="Temp", owner=user)
        await service.issue_api_key(db, org_id=org.id, name="k", scopes=[sc.RUNS_READ])
        db.add(models.UsageCounter(org_id=org.id, period="2026-10", runs=1, upload_bytes=1))
        await db.commit()
        await db.delete(org)
        await db.commit()
        left = {
            "keys": (await db.execute(select(models.ApiKey))).all(),
            "members": (await db.execute(select(models.Membership))).all(),
            "usage": (await db.execute(select(models.UsageCounter))).all(),
            "users": (await db.execute(select(models.User))).all(),
        }
        return {k: len(v) for k, v in left.items()}

    assert run(_with_session(migrated, go)) == {"keys": 0, "members": 0, "usage": 0, "users": 1}


def test_datetimes_roundtrip_as_utc_aware(migrated: Settings):
    when = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)

    async def go(db):
        db.add(models.MagicLink(token_hash="a" * 64, email="x@example.test", expires_at=when))
        await db.commit()
        return (await db.execute(select(models.MagicLink))).scalar_one().expires_at

    got = run(_with_session(migrated, go))
    assert got == when and got.tzinfo is not None


def test_run_record_exposes_report_sha256_dict():
    rec = models.RunRecord(run_id="r", org_id="o", pipeline="p", status="SUCCESS",
                           sha256_html="h", sha256_md="m", sha256_json="j")
    assert rec.report_sha256 == {"html": "h", "md": "m", "json": "j"}


def test_default_org_is_idempotent(migrated: Settings):
    async def go(db):
        a = await service.ensure_default_org(db)
        await db.commit()
        b = await service.ensure_default_org(db)
        count = len((await db.execute(select(models.Org))).all())
        return a.id, b.id, a.plan, count

    assert run(_with_session(migrated, go)) == ("default", "default", "selfhost", 1)


# ---------------------------------------------------------------- ULID
def test_ulid_is_26_chars_crockford():
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", new_ulid())


def test_ulid_strictly_increasing_even_within_one_millisecond():
    ids = [new_ulid(now_ms=1_800_000_000_000) for _ in range(2000)]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_ulid_sorts_by_time():
    assert new_ulid(now_ms=1_900_000_000_000) < new_ulid(now_ms=1_900_000_000_001)


# ---------------------------------------------------------------- klucze i tokeny
def test_api_key_format_prefix_and_hash():
    raw, prefix, key_hash = security.new_api_key()
    assert raw.startswith("daas_") and raw[5:13] == prefix and len(prefix) == 8
    assert security.parse_api_key(raw) == prefix
    assert key_hash == security.sha256_hex(raw) and raw not in key_hash


@pytest.mark.parametrize("bad", [None, "", "x", "daas_", "daas_zzzzzzzz_" + "a" * 40, "daas_12345678_short",
                                 "DAAS_12345678_" + "a" * 40, "daas_12345678-" + "a" * 40])
def test_malformed_api_keys_have_no_prefix(bad):
    assert security.parse_api_key(bad) is None


def test_api_keys_are_unique():
    assert len({security.new_api_key()[0] for _ in range(200)}) == 200


def test_token_hash_roundtrip():
    token, digest = security.new_token()
    assert len(token) >= 43 and digest == security.sha256_hex(token)


def test_session_cookie_signature():
    cookie = security.sign_session_id("abc", "s" * 40)
    assert security.unsign_session_cookie(cookie, "s" * 40) == "abc"
    assert security.unsign_session_cookie(cookie, "t" * 40) is None
    assert security.unsign_session_cookie(cookie + "0", "s" * 40) is None
    assert security.unsign_session_cookie("abc", "s" * 40) is None
    assert security.unsign_session_cookie(None, "s" * 40) is None
    assert security.unsign_session_cookie(cookie, None) is None


def test_webhook_secret_is_derived_not_stored():
    a = security.webhook_signing_secret("w1", "k" * 40)
    assert a == security.webhook_signing_secret("w1", "k" * 40)
    assert a != security.webhook_signing_secret("w2", "k" * 40)
    assert a != security.webhook_signing_secret("w1", "j" * 40)
    assert security.sign_payload(b"{}", a).startswith("sha256=") and len(security.sign_payload(b"{}", a)) == 71


def test_issue_api_key_stores_only_the_hash(migrated: Settings):
    async def go(db):
        org = await service.create_org(db, name="K", owner=None)
        key, raw = await service.issue_api_key(db, org_id=org.id, name=" ci ", scopes=[sc.RUNS_READ, sc.RUNS_READ])
        await db.commit()
        dump = [dict(r._mapping) for r in (await db.execute(text("SELECT * FROM api_keys"))).all()]
        return key, raw, dump

    key, raw, dump = run(_with_session(migrated, go))
    assert key.scopes == [sc.RUNS_READ] and key.name == "ci"
    assert raw not in repr(dump) and key.key_hash == security.sha256_hex(raw)


def test_issue_api_key_rejects_unknown_scope(migrated: Settings):
    async def go(db):
        org = await service.create_org(db, name="K", owner=None)
        await service.issue_api_key(db, org_id=org.id, name="x", scopes=["root:all"])

    with pytest.raises(ValueError, match="unknown scopes"):
        run(_with_session(migrated, go))


def test_api_key_prefix_is_unique_in_db(migrated: Settings):
    async def go(db):
        org = await service.create_org(db, name="K", owner=None)
        for _ in range(2):
            db.add(models.ApiKey(org_id=org.id, name="k", prefix="deadbeef", key_hash="h", scopes=[]))
        await db.commit()

    with pytest.raises(IntegrityError):
        run(_with_session(migrated, go))


def test_expiry_in_days():
    assert service.expiry_in_days(None) is None
    assert timedelta(days=29) < service.expiry_in_days(30) - utcnow() <= timedelta(days=30)


# ---------------------------------------------------------------- role i zakresy
def test_role_scopes_are_nested():
    assert sc.ROLE_SCOPES["member"] < sc.ROLE_SCOPES["admin"] < sc.ROLE_SCOPES["owner"]


def test_member_cannot_manage_keys_or_members():
    for scope in (sc.KEYS_MANAGE, sc.MEMBERS_MANAGE, sc.WEBHOOKS_MANAGE, sc.AUDIT_READ, sc.ORG_MANAGE):
        assert scope not in sc.ROLE_SCOPES["member"]
    for scope in (sc.UPLOADS_WRITE, sc.RUNS_WRITE, sc.RUNS_READ, sc.REPORTS_READ):
        assert scope in sc.ROLE_SCOPES["member"]


def test_only_owner_can_delete_org():
    assert sc.ORG_DELETE in sc.ROLE_SCOPES["owner"] and sc.ORG_DELETE not in sc.ROLE_SCOPES["admin"]
    assert sc.ORG_DELETE not in sc.KEY_SCOPES


# ---------------------------------------------------------------- e-mail i slug
@pytest.mark.parametrize("email", ["a@example.test", "first.last+tag@sub.example.test"])
def test_valid_emails(email):
    assert service.is_valid_email(email)


@pytest.mark.parametrize("email", ["", "a", "a@b", "a b@example.test", "@example.test", "a@@example.test",
                                   "a@example.test " + "x" * 300])
def test_invalid_emails(email):
    assert not service.is_valid_email(email)


@pytest.mark.parametrize(("name", "slug"), [("Acme Sp. z o.o.", "acme-sp-z-o-o"), ("  ", "org"),
                                             ("Łódź Żurek", "lodz-zurek"), ("A" * 100, "a" * 60)])
def test_slugify(name, slug):
    assert service.slugify(name) == slug


# ---------------------------------------------------------------- CLI create-org
def test_cli_create_org_prints_a_working_one_time_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    db_file = tmp_path / "cli_org.db"
    monkeypatch.setenv("DAAS_CONTROL_DB_URL", f"sqlite+aiosqlite:///{db_file}")
    reset_settings_cache()
    assert cli_main(["create-org", "--name", "Acme", "--owner-email", "Boss@Example.TEST"]) == 0
    out = capsys.readouterr().out
    fields = dict(line.split(":", 1) for line in out.strip().splitlines())
    raw, org_id = fields["api_key"].strip(), fields["org_id"].strip()
    assert security.parse_api_key(raw) == fields["prefix"].strip()

    s = Settings(_env_file=None, daas_control_db_url=f"sqlite+aiosqlite:///{db_file}")

    async def go(db):
        key = (await db.execute(select(models.ApiKey))).scalar_one()
        member = (await db.execute(select(models.Membership))).scalar_one()
        user = (await db.execute(select(models.User))).scalar_one()
        return key, member, user

    key, member, user = run(_with_session(s, go))
    assert key.org_id == org_id == member.org_id and member.role == "owner"
    assert user.email == "boss@example.test"
    assert key.key_hash == security.sha256_hex(raw) and set(key.scopes) == set(sc.KEY_SCOPES)
    assert raw not in db_file.read_bytes().decode("latin-1")


@pytest.mark.parametrize("args", [["--name", "X", "--owner-email", "not-an-email"],
                                  ["--name", "  ", "--owner-email", "a@example.test"]])
def test_cli_create_org_validates_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args):
    monkeypatch.setenv("DAAS_CONTROL_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'v.db'}")
    reset_settings_cache()
    with pytest.raises(SystemExit):
        cli_main(["create-org", *args])


def test_cli_requires_a_command():
    with pytest.raises(SystemExit):
        cli_main([])
