"""Audyt per organizacja: wpis `AuditEvent` w control DB + linia w pliku audytu najemcy.

`meta` przechodzi przez allowliste (`AUDIT_META_KEYS`) i ograniczenie typow wartosci — funkcja nie ma jak zapisac
nazwy pliku, tresci, sciezki, adresu e-mail ani klucza API, bo takich pol nie przyjmuje. Blad zapisu pliku
jest glosno logowany i nie przerywa zapytania (jak w audycie legacy).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.control.ids import new_ulid
from app.control.models import AuditEvent
from app.control.tenancy import InvalidOrgId, TenantPaths
from app.core.config import Settings
from app.core.observability import get_request_id
from app.core.policy import append_audit_line

log = logging.getLogger(__name__)

#: Jedyne klucze, ktore moga trafic do `AuditEvent.meta`. Cokolwiek innego jest odrzucane.
AUDIT_META_KEYS: frozenset[str] = frozenset({
    "pipeline", "run_id", "job_id", "upload_id", "kind", "bytes", "sha256", "role", "scope", "scopes",
    "reason", "status", "status_code", "attempt", "delivery_id", "event", "webhook_id", "key_prefix",
    "plan", "retention_days", "llm_narrative", "fmt", "ok", "error_type", "email_hash", "active", "count",
    "tool", "duration_ms", "key_id",  # mcp.tool: nazwa narzedzia, wynik, czas, klucz (bez argumentow)
    "sent", "recipients", "skipped_reason", "fields",  # schedule.*: przypomnienia i powiadomienia
    "client_id", "grant", "resource", "prompt", "channel",  # oauth.*, mcp.resource / mcp.prompt, powiadomienia czatu
    "period_from", "period_to", "weeks",  # job.created z zakresem dat; data-health
})

#: Akcje zapisywane przez API v1 (stabilne nazwy; sa czescia kontraktu audytu).
AUDIT_ACTIONS_V1: frozenset[str] = frozenset({
    "org.created", "org.updated", "org.deleted", "member.added", "member.invited", "member.removed", "member.role_changed", "invitation.revoked", "key.created", "key.revoked",
    "upload.created", "job.created", "job.cancelled", "run.finished", "webhook.created", "webhook.deleted",
    "upload.purged", "report.purged", "webhook.delivery", "webhook.delivered", "webhook.failed", "auth.login", "auth.logout", "auth_denied",
    "upload.purge_failed", "report.purge_failed",
    "mcp.tool", "mcp.resource", "mcp.prompt",
    "schedule.updated", "schedule.reminder", "schedule.auto_run_skipped", "schedule.notified",
    "schedule.webhook_notified",
    "oauth.client_registered", "oauth.consent", "oauth.token_issued", "oauth.token_revoked",
    "restore.sessions_deleted", "restore.magic_links_consumed", "restore.oauth_codes_expired",
    "restore.api_keys_revoked", "restore.oauth_grants_revoked", "restore.oauth_tokens_revoked",
    "restore.oauth_clients_revoked", "restore.invitations_deleted", "restore.webhooks_disabled",
    "restore.reminders_disabled", "restore.schedule_webhooks_disabled",
    "restore.jobs_cancelled", "restore.run_notifications_disabled", "restore.auto_runs_disabled",
    "restore.member_removed", "restore.member_demoted",
    "restore.reconcile.org_deleted", "restore.reconcile.user_deleted", "restore.reconcile.email_updated",
    "restore.reconcile.member_removed", "restore.reconcile.member_demoted", "restore.reconcile.invitation_deleted",
    "restore.reconcile.key_revoked", "restore.reconcile.grant_revoked", "restore.reconcile.token_revoked",
    "restore.reconcile.client_revoked", "restore.reconcile.webhook_deleted", "restore.reconcile.webhook_updated",
    "restore.reconcile.schedule_deleted", "restore.reconcile.schedule_updated", "restore.reconcile.upload_deleted",
    "restore.reconcile.run_deleted", "restore.reconcile.report_purged", "restore.reconcile.job_cancelled",
    "restore.reconcile.owner_resolved",
})

_MAX_STR = 200
_MAX_ITEMS = 20


def _clean_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:_MAX_STR]
    if isinstance(value, list | tuple | set | frozenset):
        items = sorted(value) if isinstance(value, set | frozenset) else list(value)
        return [_clean_value(v) for v in items[:_MAX_ITEMS] if isinstance(v, str | int | float | bool)]
    return str(value)[:_MAX_STR]


def filter_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Zostawia tylko klucze z allowlisty i wartosci prostych typow."""
    if not meta:
        return {}
    return {k: _clean_value(v) for k, v in meta.items() if k in AUDIT_META_KEYS}


async def record_event(
    db: AsyncSession,
    request: Request | None,
    *,
    org_id: str | None,
    actor_type: str,
    actor_id: str | None,
    action: str,
    target: str | None = None,
    meta: dict[str, Any] | None = None,
    ip: str | None = None,
    settings: Settings | None = None,
) -> AuditEvent:
    """Dodaje `AuditEvent` do sesji (commit robi wolajacy) i dopisuje linie do pliku audytu najemcy.

    Poza zadaniem HTTP (executor, dostawa webhookow) `request` to None, a `settings` podaje wolajacy.
    """
    if action not in AUDIT_ACTIONS_V1:
        raise ValueError(f"unknown audit action: {action!r}")
    if ip is None and request is not None and request.client:
        ip = request.client.host
    if settings is None and request is not None:
        settings = request.app.state.settings
    clean = filter_meta(meta)
    event = AuditEvent(org_id=org_id, actor_type=actor_type, actor_id=actor_id, action=action,
                       target=target[:80] if target else None, ip=ip, meta=clean)
    event.id = new_ulid()  # id nadajemy od razu: plik i baza maja ten sam identyfikator
    db.add(event)
    _write_line(settings, event, clean)
    return event


def _write_line(settings: Settings | None, event: AuditEvent, meta: dict[str, Any]) -> None:
    if settings is None or event.org_id is None:
        return
    try:
        paths = TenantPaths.for_org(settings, event.org_id)
        if not paths.root.exists():
            return  # organizacja bez katalogu danych (jeszcze nie uzywana): audyt jest w control DB
        append_audit_line(paths.audit_log, {
            "event_id": event.id, "ts": round(time.time(), 3), "action": event.action,
            "decision": "deny" if event.action == "auth_denied" else "allow",
            "org_id": event.org_id, "actor_type": event.actor_type, "actor_id": event.actor_id,
            "route": event.target, "client_ip": event.ip, "request_id": get_request_id(), "meta": meta,
        })
    except InvalidOrgId:
        log.warning("audit file skipped: invalid org id")
    except Exception:  # noqa: BLE001
        log.exception("audit file write failed action=%s", event.action)
