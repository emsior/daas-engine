"""Organizacje: onboarding (`POST /orgs`), odczyt, zmiana ustawien, usuniecie."""
from __future__ import annotations

import asyncio
import shutil

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError
from app.api.v1.deps import OrgContext, Principal, get_principal, org_access
from app.api.v1.schemas import OrgCreate, OrgOut, OrgPatch
from app.control import scopes as sc
from app.control import service
from app.control.audit import record_event
from app.control.db import get_db
from app.control.models import Job, Org
from app.control.tenancy import TenantPaths
from app.core.ratelimit import too_many

router = APIRouter(tags=["orgs"])

ORG_CREATE_PER_MIN = 5

_READ = org_access(sc.ORG_READ)
_MANAGE = org_access(sc.ORG_MANAGE)
_DELETE = org_access(sc.ORG_DELETE)


@router.post(
    "/orgs", status_code=201, response_model=OrgOut, summary="Create an organization (onboarding)",
    description="Creates an organization with the signed-in user as owner. `dpa_accepted` must be `true`: "
                "the data processing agreement (DPA) version `v1` is accepted at creation. Needs a user session.",
    responses={403: {"description": "Not a user session, or selfhost mode"}, 422: {"description": "DPA not accepted"}},
)
async def create_org(body: OrgCreate, request: Request, response: Response,
                     principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> OrgOut:
    state = request.app.state
    if not state.settings.cloud:
        raise ProblemError(403, "orgs_disabled_in_selfhost", "selfhost mode has a single organization")
    if principal.user is None:
        raise ProblemError(403, "user_session_required", "this operation requires a signed-in user")
    if not body.dpa_accepted:
        raise ProblemError(422, "dpa_required", "the data processing agreement (DPA) must be accepted")
    wait = state.limiter.hit("org_create", principal.user.id, ORG_CREATE_PER_MIN)
    if wait > 0:
        raise too_many(wait)
    org = await service.create_org(
        db, name=body.name, owner=principal.user, locale=body.locale, dpa_version=body.dpa_version,
        retention_days=state.settings.daas_retention_days)
    if principal.session is not None and principal.session.org_id is None:
        principal.session.org_id = org.id
        db.add(principal.session)
    await record_event(db, request, org_id=org.id, actor_type="user", actor_id=principal.user.id,
                       action="org.created", target=f"org:{org.id}", meta={"plan": org.plan})
    await db.commit()
    response.headers["Location"] = f"/api/v1/orgs/{org.id}"
    return OrgOut.model_validate(org)


@router.get("/orgs/{org_id}", response_model=OrgOut, summary="Get an organization")
async def get_org(ctx: OrgContext = Depends(_READ)) -> OrgOut:
    return OrgOut.model_validate(ctx.org)


@router.patch("/orgs/{org_id}", response_model=OrgOut, summary="Update organization settings",
              description="Name, locale, upload retention (days) and the opt-in for LLM-generated summaries.")
async def patch_org(body: OrgPatch, request: Request, ctx: OrgContext = Depends(_MANAGE),
                    db: AsyncSession = Depends(get_db)) -> OrgOut:
    org = await db.get(Org, ctx.org_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        setattr(org, field, value)
    meta = {k: v for k, v in changes.items() if k in {"retention_days", "llm_narrative"}}
    await record_event(db, request, org_id=org.id, actor_type=ctx.principal.actor_type,
                       actor_id=ctx.principal.actor_id, action="org.updated", target=f"org:{org.id}", meta=meta)
    await db.commit()
    return OrgOut.model_validate(org)


@router.delete("/orgs/{org_id}", status_code=204, summary="Delete an organization (owner only)",
               description="Deletes the organization, its control-plane records and its data directory. "
                           "Refused while jobs are queued or running, and for the selfhost `default` organization.")
async def delete_org(request: Request, ctx: OrgContext = Depends(_DELETE),
                     db: AsyncSession = Depends(get_db)) -> Response:
    state = request.app.state
    org = await db.get(Org, ctx.org_id)
    if org.plan == "selfhost":
        raise ProblemError(403, "org_protected", "the selfhost organization cannot be deleted")
    active = (await db.execute(select(func.count()).select_from(Job).where(
        Job.org_id == org.id, Job.status.in_(("queued", "running"))))).scalar_one()
    if active:
        raise ProblemError(409, "org_busy", "wait for running jobs to finish or cancel them first")
    org_id, actor = org.id, ctx.principal
    await record_event(db, request, org_id=None, actor_type=actor.actor_type, actor_id=actor.actor_id,
                       action="org.deleted", target=f"org:{org_id}")
    await db.delete(org)
    await db.commit()
    state.tenants.forget(org_id)
    root = TenantPaths.for_org(state.settings, org_id).root
    if root.exists():
        await asyncio.to_thread(shutil.rmtree, root)
    return Response(status_code=204)
