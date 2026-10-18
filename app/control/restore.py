"""Offline quarantine of restored credentials; every mutation and audit event is one transaction."""
from __future__ import annotations

from sqlalchemy import delete, func, select, true, update

from app.control.audit import record_event
from app.control.db import create_engine, make_sessionmaker
from app.control.models import (
    ApiKey,
    Invitation,
    Job,
    MagicLink,
    OAuthClient,
    OAuthCode,
    OAuthGrant,
    OAuthToken,
    OrgSchedule,
    Session,
    Webhook,
)
from app.control.types import utcnow
from app.core.config import Settings


async def restore_quarantine(settings: Settings, *, dry_run: bool = False,
                             revoke_all: bool = False) -> dict[str, int]:
    """Run with every API process stopped, against an already migrated restored control database.

    Counts are affected rows, including expired but unconsumed/unrevoked credentials. Global, count-only
    audit events stay in the database transaction: no tenant audit file can outlive a rolled-back action.
    Dry-run only reads counts, including no audit insertion. It never upgrades the database schema.
    """
    now = utcnow()
    operations = [
        ("sessions_deleted", Session, true(), None),
        ("magic_links_consumed", MagicLink, MagicLink.used_at.is_(None), {"used_at": now}),
        ("oauth_codes_expired", OAuthCode, OAuthCode.status.in_(("pending", "approved")), {"status": "expired"}),
    ]
    if revoke_all:
        operations.extend([
            ("api_keys_revoked", ApiKey, ApiKey.revoked_at.is_(None), {"revoked_at": now}),
            ("oauth_grants_revoked", OAuthGrant, OAuthGrant.revoked_at.is_(None),
             {"revoked_at": now, "revocation_reason": "restore_quarantine"}),
            ("oauth_tokens_revoked", OAuthToken, OAuthToken.revoked_at.is_(None), {"revoked_at": now}),
            ("oauth_clients_revoked", OAuthClient, OAuthClient.revoked_at.is_(None), {"revoked_at": now}),
            ("invitations_deleted", Invitation, Invitation.accepted_at.is_(None), None),
            ("webhooks_disabled", Webhook, Webhook.active.is_(True), {"active": False}),
            ("reminders_disabled", OrgSchedule, OrgSchedule.reminder_enabled.is_(True),
             {"reminder_enabled": False, "next_reminder_at": None}),
            ("schedule_webhooks_disabled", OrgSchedule, OrgSchedule.notify_webhook_url.is_not(None),
             {"notify_webhook_url": None}),
            ("jobs_cancelled", Job, Job.status == "queued",
             {"status": "cancelled", "error_code": "restore_quarantine", "finished_at": now}),
            ("run_notifications_disabled", OrgSchedule, OrgSchedule.notify_on_finish.is_(True),
             {"notify_on_finish": False}),
            ("auto_runs_disabled", OrgSchedule, OrgSchedule.auto_run_on_upload.is_(True),
             {"auto_run_on_upload": False}),
        ])
    engine = create_engine(settings)
    try:
        async with make_sessionmaker(engine)() as db:
            async with db.begin():
                counts = {}
                for category, model, condition, values in operations:
                    if dry_run:
                        count = (await db.execute(select(func.count()).select_from(model).where(condition))).scalar_one()
                    else:
                        statement = delete(model) if values is None else update(model).values(**values)
                        result = await db.execute(statement.where(condition).execution_options(synchronize_session=False))
                        count = result.rowcount
                    counts[category] = count
                    if count and not dry_run:
                        await record_event(db, None, org_id=None, actor_type="system", actor_id=None,
                                           action=f"restore.{category}", target=model.__tablename__, meta={"count": count})
            return counts
    finally:
        await engine.dispose()
