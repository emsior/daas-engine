"""Czlonkowie i zaproszenia. Brak roli w organizacji = 403 (a nie 404), zob. `org_access(members_route=True)`."""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError
from app.api.v1.deps import OrgContext, org_access
from app.api.v1.schemas import InvitationCreate, InvitationOut, MemberCreate, MemberOut
from app.control import scopes as sc
from app.control.audit import record_event
from app.control.db import get_db
from app.control.mailer import invitation_mail
from app.control.models import Invitation, Membership, User
from app.control.security import new_token
from app.control.types import utcnow

log = logging.getLogger(__name__)
router = APIRouter(tags=["members"])

INVITATION_TTL = timedelta(days=7)

_READ = org_access(sc.ORG_READ, members_route=True)
_MANAGE = org_access(sc.MEMBERS_MANAGE, members_route=True)


def _check_role_grant(ctx: OrgContext, role: str) -> None:
    """Nadawanie roli `owner` tylko przez wlasciciela (sesja); klucze API i admini nie eskaluja uprawnien."""
    if role == "owner" and ctx.role != "owner":
        raise ProblemError(403, "forbidden", "only an owner can grant the owner role")


@router.get("/orgs/{org_id}/members", response_model=list[MemberOut], summary="List members")
async def list_members(ctx: OrgContext = Depends(_READ), db: AsyncSession = Depends(get_db)) -> list[MemberOut]:
    rows = await db.execute(
        select(Membership, User).join(User, User.id == Membership.user_id)
        .where(Membership.org_id == ctx.org_id).order_by(Membership.created_at, User.email))
    return [MemberOut(user_id=u.id, email=u.email, name=u.name, role=m.role, created_at=m.created_at)
            for m, u in rows.all()]


@router.post(
    "/orgs/{org_id}/members", status_code=201, response_model=MemberOut, summary="Add an existing user",
    description="Adds a user who already has an account. For a new address use `POST /orgs/{org_id}/invitations`.",
    responses={404: {"description": "No user with this address"}, 409: {"description": "Already a member"}},
)
async def add_member(body: MemberCreate, request: Request, ctx: OrgContext = Depends(_MANAGE),
                     db: AsyncSession = Depends(get_db)) -> MemberOut:
    _check_role_grant(ctx, body.role)
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is None:
        raise ProblemError(404, "user_not_found", "no user with this e-mail address; send an invitation instead")
    if await db.get(Membership, (ctx.org_id, user.id)) is not None:
        raise ProblemError(409, "already_member", "the user is already a member of this organization")
    membership = Membership(org_id=ctx.org_id, user_id=user.id, role=body.role)
    db.add(membership)
    await record_event(db, request, org_id=ctx.org_id, actor_type=ctx.principal.actor_type,
                       actor_id=ctx.principal.actor_id, action="member.added", target=f"user:{user.id}",
                       meta={"role": body.role})
    await db.commit()
    await db.refresh(membership)
    return MemberOut(user_id=user.id, email=user.email, name=user.name, role=membership.role,
                     created_at=membership.created_at)


@router.post(
    "/orgs/{org_id}/invitations", status_code=201, response_model=InvitationOut, summary="Invite by e-mail",
    description="Stores an invitation (valid 7 days) and e-mails the address. The invitation is accepted when the "
                "address signs in. Repeating it for the same address refreshes the existing invitation.",
    responses={409: {"description": "Already a member"}},
)
async def invite(body: InvitationCreate, request: Request, ctx: OrgContext = Depends(_MANAGE),
                 db: AsyncSession = Depends(get_db)) -> InvitationOut:
    _check_role_grant(ctx, body.role)
    existing_user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if existing_user is not None and await db.get(Membership, (ctx.org_id, existing_user.id)) is not None:
        raise ProblemError(409, "already_member", "the user is already a member of this organization")
    _token, digest = new_token()
    now = utcnow()
    invitation = (await db.execute(select(Invitation).where(
        Invitation.org_id == ctx.org_id, Invitation.email == body.email, Invitation.accepted_at.is_(None),
    ))).scalar_one_or_none()
    if invitation is None:
        invitation = Invitation(org_id=ctx.org_id, email=body.email, role=body.role, token_hash=digest,
                                invited_by=ctx.principal.actor_id, expires_at=now + INVITATION_TTL)
        db.add(invitation)
    else:
        invitation.role, invitation.token_hash, invitation.expires_at = body.role, digest, now + INVITATION_TTL
    await db.flush()
    await record_event(db, request, org_id=ctx.org_id, actor_type=ctx.principal.actor_type,
                       actor_id=ctx.principal.actor_id, action="member.invited", target=f"invitation:{invitation.id}",
                       meta={"role": body.role, "email_hash": hashlib.sha256(body.email.encode()).hexdigest()[:16]})
    await db.commit()
    mailer = request.app.state.mailer
    try:
        await run_in_threadpool(mailer.send, invitation_mail(
            request.app.state.settings, body.email, ctx.org.name, body.role, ctx.org.locale))
    except Exception:  # noqa: BLE001
        log.exception("invitation e-mail failed")  # zaproszenie istnieje; zostanie przyjete przy logowaniu
    return InvitationOut(id=invitation.id, email=invitation.email, role=invitation.role,
                         created_at=invitation.created_at, expires_at=invitation.expires_at)
