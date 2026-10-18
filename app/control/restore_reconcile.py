"""Restrictive offline recovery. Control changes commit together; runtime cleanup follows."""
from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import tarfile
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

import duckdb
from sqlalchemy import Boolean, delete, select, update
from sqlalchemy import create_engine as sync_engine

from app.control import models as m
from app.control.audit import record_event
from app.control.db import create_engine, make_sessionmaker
from app.control.ids import new_ulid
from app.control.retention import (
    REPORT_PATH_COLUMNS,
    RUN_TABLES,
    _purge_tenant,
    _purge_uploads_files,
)
from app.control.tenancy import TenantPaths
from app.control.types import utcnow
from app.core.config import Settings


class ReconcileError(ValueError):
    """A safe, value-free operator error; never attach raw database diagnostics."""


# Select only columns used by this version, not ORM objects from the newer snapshot.
_COLUMNS = {
    m.Org: ("id", "name", "plan", "retention_days", "locale", "llm_narrative"),
    m.User: ("id", "email", "name", "locale"),
    m.Membership: ("org_id", "user_id", "role"),
    m.Invitation: ("id", "org_id", "accepted_at", "expires_at"),
    m.ApiKey: ("id", "org_id", "revoked_at"),
    m.OAuthClient: ("client_id", "revoked_at"),
    m.OAuthGrant: ("id", "client_id", "org_id", "revoked_at"),
    m.OAuthToken: ("id", "client_id", "org_id", "grant_id", "revoked_at"),
    m.Webhook: ("id", "org_id", "active", "url", "events"),
    m.OrgSchedule: ("org_id", "reminder_enabled", "notify_on_finish", "auto_run_on_upload", "notify_webhook_url",
                    "reminder_weekday", "reminder_hour", "timezone", "reminder_skip_if_recent_upload",
                    "notify_webhook_kind"),
    m.Upload: ("id", "org_id"),
    m.RunRecord: ("run_id", "org_id", "report_purged_at"),
    m.Job: ("id", "org_id", "status"),
}
_RANK = {"member": 0, "admin": 1, "owner": 2}
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_ULID = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")


def _key(model, row):
    keys = [row[c.name] for c in model.__table__.primary_key.columns]
    for value in keys:
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise ReconcileError("invalid record identifier; keep the API stopped")
    return tuple(keys) if len(keys) > 1 else keys[0]


def _read_pre(database: Path):
    uri = database.resolve().as_uri() + "?mode=ro&immutable=1"
    engine = sync_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True))
    try:
        with engine.connect() as db:
            db.exec_driver_sql("PRAGMA query_only=ON")
            db.exec_driver_sql("PRAGMA trusted_schema=OFF")
            if db.exec_driver_sql("PRAGMA integrity_check").all() != [("ok",)]:
                raise ReconcileError("pre-restore database integrity check failed")
            if db.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise ReconcileError("pre-restore database foreign key check failed")
            result = {}
            for model, names in _COLUMNS.items():
                columns = [model.__table__.c[name] for name in names]
                for column in columns:
                    if isinstance(column.type, Boolean):
                        invalid = db.exec_driver_sql(
                            f'SELECT 1 FROM "{model.__tablename__}" WHERE "{column.name}" NOT IN (0,1) '  # noqa: S608
                            f'OR "{column.name}" IS NULL LIMIT 1').first()
                        if invalid is not None:
                            raise ReconcileError("invalid pre-restore boolean value")
                rows = db.execute(select(*columns)).mappings()
                result[model] = {_key(model, row): dict(row) for row in rows}
            for row in result[m.Membership].values():
                if row["role"] not in _RANK:
                    raise ReconcileError("invalid pre-restore membership role")
            for row in result[m.Job].values():
                if row["status"] not in {"queued", "running", "succeeded", "failed", "cancelled"}:
                    raise ReconcileError("invalid pre-restore job status")
            for row in result[m.Webhook].values():
                if not isinstance(row["events"], list) or not all(isinstance(x, str) for x in row["events"]):
                    raise ReconcileError("invalid pre-restore webhook events")
            return result
    finally:
        engine.dispose()


def read_archive(archive: Path):
    """Hash the same file handle that is unpacked. Never extract archive-supplied paths."""
    archive = Path(archive)
    sidecar = Path(str(archive) + ".sha256").read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *]([^\r\n]+)", sidecar)
    if not match or match[2] != archive.name:
        raise ReconcileError("invalid pre-restore checksum file")
    with archive.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != match[1].lower():
            raise ReconcileError("pre-restore archive checksum mismatch")
        source.seek(0)
        with tempfile.TemporaryDirectory(prefix="daas-reconcile-") as temporary:
            database = Path(temporary) / "control.db"
            found = False
            with tarfile.open(fileobj=source, mode="r:gz") as tar:
                for member in tar:
                    path = PurePosixPath(member.name)
                    if (path.is_absolute() or ".." in path.parts or "\\" in member.name
                            or not path.parts or path.parts[0] not in {"control", "runtime"}):
                        raise ReconcileError("unsafe pre-restore archive entry")
                    if path.as_posix() in {"control/control.db-wal", "control/control.db-journal"} and member.size:
                        raise ReconcileError("pre-restore database needs recovery; use a consistent cold copy")
                    if path.as_posix() != "control/control.db":
                        continue
                    if found or not member.isfile():
                        raise ReconcileError("duplicate or non-regular pre-restore database entry")
                    found = True
                    with tar.extractfile(member) as content, database.open("wb") as output:
                        shutil.copyfileobj(content, output)
            if not found:
                raise ReconcileError("pre-restore archive has no control database")
            return _read_pre(database)


@dataclass
class Action:
    category: str
    model: type
    row: object
    values: dict | None = None

    @property
    def identifier(self):
        key = _key(self.model, self.row.__dict__)
        return ":".join(key) if isinstance(key, tuple) else key


@dataclass
class ReconcileReport:
    categories: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    blocked: list[str] = field(default_factory=list)
    owner_candidates: dict[str, list[str]] = field(default_factory=dict)
    cleanup_failed: list[str] = field(default_factory=list)
    applied: bool = False

    def lines(self):
        yield f"mode: {'blocked' if self.blocked else 'applied' if self.applied else 'dry-run'}"
        for category, identifiers in sorted(self.categories.items()):
            yield f"{category}: {len(identifiers)}"
            yield f"  ids: {', '.join(sorted(identifiers))}"
        if self.blocked:
            yield f"owner_conflicts: {len(self.blocked)}"
            yield f"  ids: {', '.join(sorted(self.blocked))}"
            for org_id in sorted(self.blocked):
                yield f"owner_candidates: {org_id}"
                yield f"  ids: {', '.join(sorted(self.owner_candidates[org_id]))}"
        if self.cleanup_failed:
            yield f"cleanup_failed: {len(self.cleanup_failed)}"
            yield f"  ids: {', '.join(sorted(self.cleanup_failed))}"


def _plan(current, pre, now, resolutions):
    if current[m.Org] and not current[m.Org].keys() & pre[m.Org].keys():
        raise ReconcileError("pre-restore copy does not belong to this database history; use RUNBOOK section 5.4")
    report = ReconcileReport()
    actions = []
    deleted_orgs = current[m.Org].keys() - pre[m.Org].keys()
    deleted_users = current[m.User].keys() - pre[m.User].keys()
    for org_id in current[m.Org].keys() - deleted_orgs:
        retained_owners = [row for key, row in current[m.Membership].items()
                           if row.org_id == org_id and row.user_id not in deleted_users and row.role == "owner"
                           and key in pre[m.Membership] and pre[m.Membership][key]["role"] == "owner"]
        if not retained_owners:
            report.blocked.append(org_id)
            report.owner_candidates[org_id] = [
                row.user_id for key, row in current[m.Membership].items()
                if row.org_id == org_id and row.user_id in current[m.User] and row.user_id not in deleted_users
                and key in pre[m.Membership] and pre[m.Membership][key]["role"] == "owner"
            ]

    # Check every explicit choice before planning any promotion or clearing a conflict.
    for org_id, user_id in resolutions.items():
        if org_id not in report.blocked or user_id not in report.owner_candidates[org_id]:
            raise ReconcileError("owner resolution requires a conflicting organization and a retained pre-restore owner")
    report.blocked = [org_id for org_id in report.blocked if org_id not in resolutions]

    def add(category, model, row, values=None):
        action = Action(category, model, row, values)
        report.categories[category].append(action.identifier)
        actions.append(action)

    for org_id, user_id in resolutions.items():
        add("owner_resolved", m.Membership, current[m.Membership][(org_id, user_id)], {"role": "owner"})

    for model, rows in current.items():
        for key, row in rows.items():
            org_id = getattr(row, "org_id", row.id if model is m.Org else None)
            if org_id in report.blocked or (org_id in deleted_orgs and model is not m.Org):
                continue
            old = pre[model].get(key)
            if model is m.Org:
                if old is None:
                    add("org_deleted", model, row)
                elif any(getattr(row, name) != old[name] for name in _COLUMNS[model] if name != "id"):
                    report.categories["org_differences"].append(row.id)
            elif model is m.User:
                if old is None:
                    add("user_deleted", model, row)
                else:
                    if row.email != old["email"]:
                        add("email_updated", model, row, {"email": old["email"]})
                    if row.name != old["name"] or row.locale != old["locale"]:
                        report.categories["user_differences"].append(row.id)
            elif model is m.Membership:
                if row.role not in _RANK:
                    raise ReconcileError("invalid restored membership role")
                if row.user_id in deleted_users or old is None:
                    add("member_removed", model, row)
                elif _RANK[old["role"]] < _RANK[row.role]:
                    add("member_demoted", model, row, {"role": old["role"]})
            elif model is m.Invitation:
                if old is None or old["accepted_at"] is not None or old["expires_at"] <= now:
                    add("invitation_deleted", model, row)
            elif model in (m.ApiKey, m.OAuthClient, m.OAuthGrant, m.OAuthToken):
                revoked = old is None or old["revoked_at"] is not None
                if model in (m.OAuthGrant, m.OAuthToken):
                    client = pre[m.OAuthClient].get(row.client_id)
                    revoked |= client is None or client["revoked_at"] is not None
                if model is m.OAuthToken and row.grant_id:
                    grant = pre[m.OAuthGrant].get(row.grant_id)
                    revoked |= grant is None or grant["revoked_at"] is not None
                if revoked and row.revoked_at is None:
                    category = {m.ApiKey: "key", m.OAuthClient: "client", m.OAuthGrant: "grant", m.OAuthToken: "token"}[model]
                    values = {"revoked_at": now}
                    if model is m.OAuthGrant:
                        values["revocation_reason"] = "restore_reconcile"
                    add(f"{category}_revoked", model, row, values)
            elif model is m.Webhook:
                if old is None:
                    add("webhook_deleted", model, row)
                else:
                    values = {name: old[name] for name in ("url", "events") if getattr(row, name) != old[name]}
                    if row.active and not old["active"]:
                        values["active"] = False
                    if values:
                        add("webhook_updated", model, row, values)
            elif model is m.OrgSchedule:
                if old is None:
                    add("schedule_deleted", model, row)
                else:
                    values = {name: False for name in ("reminder_enabled", "notify_on_finish", "auto_run_on_upload")
                              if getattr(row, name) and not old[name]}
                    if "reminder_enabled" in values:
                        values["next_reminder_at"] = None
                    if row.notify_webhook_url != old["notify_webhook_url"]:
                        values["notify_webhook_url"] = old["notify_webhook_url"]
                    if values:
                        add("schedule_updated", model, row, values)
                    if any(getattr(row, name) != old[name] for name in (
                            "reminder_weekday", "reminder_hour", "timezone", "reminder_skip_if_recent_upload",
                            "notify_webhook_kind")):
                        report.categories["schedule_differences"].append(row.org_id)
            elif model is m.Upload and old is None:
                add("upload_deleted", model, row)
            elif model is m.RunRecord:
                if old is None:
                    add("run_deleted", model, row)
                elif row.report_purged_at is None and old["report_purged_at"] is not None:
                    add("report_purged", model, row, {"report_purged_at": now})
            elif model is m.Job and row.status == "queued":
                if old is None or old["status"] in {"succeeded", "failed", "cancelled"}:
                    add("job_cancelled", model, row,
                        {"status": "cancelled", "finished_at": now, "error_code": "restore_reconcile"})
    return report, actions


class _ExistingTenant:
    """The retention connection interface, without runner construction or schema upgrades."""
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        with duckdb.connect(str(self.path)) as db:
            yield db


def _cleanup(settings, actions):
    failures = []
    for action in actions:
        if action.category not in {"org_deleted", "upload_deleted", "run_deleted", "report_purged"}:
            continue
        row = action.row
        try:
            paths = TenantPaths.for_org(settings, row.id if action.model is m.Org else row.org_id)
            if action.category == "org_deleted":
                if paths.root.exists():
                    shutil.rmtree(paths.root)
            elif action.category == "upload_deleted":
                results = _purge_uploads_files(paths, [row.path], False, row.org_id)
                if "failed" in results:
                    failures.append(action.identifier)
            elif paths.duckdb.exists():
                _, _, _, failed = _purge_tenant(_ExistingTenant(paths.duckdb), paths.reports, [row.run_id],
                                               datetime.min, False, row.org_id)
                if failed:
                    failures.append(action.identifier)
        except (OSError, RuntimeError, duckdb.Error):
            failures.append(action.identifier)
    return failures


async def restore_reconcile(settings: Settings, archive: Path, *, apply: bool = False,
                            resolve_owners: list[str] | None = None) -> ReconcileReport:
    resolutions = {}
    if resolve_owners and not apply:
        raise ReconcileError("owner resolution requires --apply")
    for choice in resolve_owners or []:
        parts = choice.split(":")
        if len(parts) != 2 or not all(_ULID.fullmatch(value) for value in parts):
            raise ReconcileError("invalid owner resolution identifiers")
        org_id, user_id = parts
        if org_id in resolutions:
            raise ReconcileError("choose only one owner resolution per organization")
        resolutions[org_id] = user_id
    pre = read_archive(archive)
    engine = create_engine(settings)
    try:
        async with make_sessionmaker(engine)() as db:
            async with db.begin():
                current = {}
                for model in _COLUMNS:
                    rows = (await db.execute(select(model))).scalars().all()
                    current[model] = {_key(model, row.__dict__): row for row in rows}
                report, actions = _plan(current, pre, utcnow(), resolutions)
                if not apply or report.blocked:
                    return report
                # Validate every runtime target before any control write.
                for action in actions:
                    if action.model in (m.Org, m.Upload, m.RunRecord):
                        org_id = action.row.id if action.model is m.Org else action.row.org_id
                        paths = TenantPaths.for_org(
                            settings, org_id)
                        expected_root = settings.tenants_root.resolve() / org_id
                        if paths.root != expected_root or any(
                                path.resolve() != path for path in (paths.uploads, paths.reports, paths.duckdb)):
                            raise ReconcileError("redirected restored runtime path; keep the API stopped")
                        if action.model is m.Upload:
                            candidate = paths.uploads / action.row.path
                            if (Path(action.row.path).name != action.row.path
                                    or candidate.resolve() != candidate):
                                raise ReconcileError("unsafe restored upload path; keep the API stopped")
                        if action.model is m.RunRecord:
                            if not paths.duckdb.exists():
                                if paths.reports.exists() and any(paths.reports.iterdir()):
                                    raise ReconcileError("report metadata unavailable; keep the API stopped")
                            else:
                                with duckdb.connect(str(paths.duckdb), read_only=True) as tenant:
                                    for table in RUN_TABLES:
                                        tenant.execute(f"SELECT run_id FROM {table} LIMIT 0")  # noqa: S608
                                    tenant.execute(f"SELECT {', '.join(REPORT_PATH_COLUMNS)} FROM runs LIMIT 0")  # noqa: S608
                # Avoid transient unique collisions when a valid source swaps e-mails or URLs.
                for action in actions:
                    if action.category == "email_updated":
                        action.row.email = f"restore-{new_ulid()}@example.invalid"
                    elif action.category == "webhook_updated" and "url" in action.values:
                        action.row.url = f"https://restore.example.invalid/{new_ulid()}"
                await db.flush()
                # Explicit statements avoid ORM delete-order surprises with cascaded users/members.
                for action in sorted(actions, key=lambda item: item.values is not None):
                    model, row = action.model, action.row
                    conditions = [column == getattr(row, column.name) for column in model.__table__.primary_key]
                    statement = delete(model) if action.values is None else update(model).values(**action.values)
                    await db.execute(statement.where(*conditions).execution_options(synchronize_session=False))
                    await record_event(db, None, org_id=None, actor_type="system", actor_id=None,
                                       action=f"restore.reconcile.{action.category}", target=action.identifier,
                                       meta={"count": 1})
                await db.flush()
            report.applied = True
        report.cleanup_failed = _cleanup(settings, actions)
        return report
    finally:
        await engine.dispose()
