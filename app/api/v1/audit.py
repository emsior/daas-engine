"""Dziennik audytu organizacji: stronicowanie kursorem po `(ts, id)` malejaco."""
from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError
from app.api.v1.deps import OrgContext, org_access
from app.api.v1.schemas import AuditOut, AuditPage
from app.control import scopes as sc
from app.control.db import get_db
from app.control.models import AuditEvent

router = APIRouter(tags=["audit"])

_READ = org_access(sc.AUDIT_READ)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_MAX_CURSOR_LEN = 120


def encode_cursor(ts: datetime, event_id: str) -> str:
    """Kursor to nieprzezroczysty napis: base64url(JSON `[ts_iso, id]`) ostatniego zdarzenia strony."""
    raw = json.dumps([ts.astimezone(UTC).isoformat(), event_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts_raw, event_id = json.loads(base64.urlsafe_b64decode(padded.encode()))
        ts = datetime.fromisoformat(ts_raw)
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 26:
            raise ValueError("bad id")
        return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)), event_id
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        raise ProblemError(422, "invalid_cursor", "the cursor is malformed") from None


@router.get(
    "/orgs/{org_id}/audit", response_model=AuditPage, summary="Audit log (newest first)",
    description="Events of this organization only, ordered by `(ts, id)` descending. Pass `next_cursor` as `cursor` "
                "to get the next page; `next_cursor` is `null` on the last page. `meta` holds allow-listed fields "
                "only (never file names, e-mail addresses or keys).",
    responses={422: {"description": "Malformed cursor or limit out of range"}},
)
async def list_audit(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = Query(default=None, max_length=_MAX_CURSOR_LEN),
    ctx: OrgContext = Depends(_READ), db: AsyncSession = Depends(get_db),
) -> AuditPage:
    query = select(AuditEvent).where(AuditEvent.org_id == ctx.org_id)
    if cursor:
        ts, event_id = decode_cursor(cursor)
        query = query.where(or_(AuditEvent.ts < ts, and_(AuditEvent.ts == ts, AuditEvent.id < event_id)))
    rows = (await db.execute(
        query.order_by(AuditEvent.ts.desc(), AuditEvent.id.desc()).limit(limit + 1))).scalars().all()
    page = rows[:limit]
    items = [AuditOut(id=e.id, ts=e.ts, actor_type=e.actor_type, actor_id=e.actor_id, action=e.action,
                      target=e.target, ip=e.ip, meta=dict(e.meta or {})) for e in page]
    next_cursor = encode_cursor(page[-1].ts, page[-1].id) if len(rows) > limit else None
    return AuditPage(items=items, next_cursor=next_cursor)
