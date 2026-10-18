"""Offline membership review and restrictive corrections after a database restore."""
from __future__ import annotations

import re

from sqlalchemy import select

from app.control import service
from app.control.audit import record_event
from app.control.db import create_engine, make_sessionmaker
from app.control.models import Membership, User
from app.core.config import Settings

_ULID = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")
_ROLES = {"member": 0, "admin": 1, "owner": 2}


class RestoreMembersError(ValueError):
    """Safe operator-facing validation error, without restored values or database details."""


def _validate_id(value: str, *, org: bool = False) -> None:
    if not (org and value == "default") and not _ULID.fullmatch(value):
        raise RestoreMembersError("invalid organization or user identifier")


def _row(membership: Membership, user: User, *, show_emails: bool) -> dict[str, str]:
    # Restored values are not automatically safe to echo, even when they came from the database.
    _validate_id(membership.org_id, org=True)
    _validate_id(user.id)
    if membership.role not in _ROLES:
        raise RestoreMembersError("invalid membership role in the restored database")
    result = {
        "org_id": membership.org_id,
        "user_id": user.id,
        "role": membership.role,
        "membership_created_at": membership.created_at.isoformat(),
        "user_created_at": user.created_at.isoformat(),
    }
    if show_emails:
        result["email"] = user.email
    return result


async def restore_members(settings: Settings, *, org_id: str | None = None, user_id: str | None = None,
                          remove: bool = False, demote_to: str | None = None,
                          show_emails: bool = False) -> list[dict[str, str]]:
    """Review or correct memberships with the API stopped; no migrations, promotions or tenant-file writes.

    Each correction and its database audit event share one transaction. The normal member helpers enforce
    the last-owner invariant without requiring an authenticated owner session for this offline command.
    E-mail is opt-in for a read-only listing and never part of a correction's output or audit event.
    """
    changing = remove or demote_to is not None
    if remove and demote_to is not None:
        raise RestoreMembersError("choose either removal or demotion")
    if changing and (org_id is None or user_id is None):
        raise RestoreMembersError("a correction requires both organization and user identifiers")
    if demote_to is not None and demote_to not in {"admin", "member"}:
        raise RestoreMembersError("demotion target must be admin or member")
    if org_id is not None:
        _validate_id(org_id, org=True)
    if user_id is not None:
        _validate_id(user_id)

    engine = create_engine(settings)
    try:
        async with make_sessionmaker(engine)() as db:
            async with db.begin():
                query = select(Membership, User).join(User, User.id == Membership.user_id)
                if org_id is not None:
                    query = query.where(Membership.org_id == org_id)
                if user_id is not None:
                    query = query.where(Membership.user_id == user_id)
                rows = (await db.execute(query.order_by(Membership.org_id, Membership.user_id))).all()
                if not changing:
                    return [_row(membership, user, show_emails=show_emails) for membership, user in rows]
                if len(rows) != 1:
                    raise RestoreMembersError("membership not found")
                membership, user = rows[0]
                result = _row(membership, user, show_emails=False)
                if remove:
                    applied = await service.delete_member_guarded(db, membership.org_id, membership.user_id)
                    action = "removed"
                else:
                    if _ROLES[demote_to] >= _ROLES[membership.role]:
                        raise RestoreMembersError("the target role must be lower than the current role")
                    applied = await service.set_member_role_guarded(
                        db, membership.org_id, membership.user_id, demote_to)
                    action = "demoted"
                    result["role"] = demote_to
                if not applied:
                    raise RestoreMembersError("the organization must keep at least one owner")
                await record_event(db, None, org_id=membership.org_id, actor_type="system", actor_id=None,
                                   action=f"restore.member_{action}", target=f"user:{membership.user_id}",
                                   meta={"role": result["role"]})
                result["action"] = action
                return [result]
    finally:
        await engine.dispose()
