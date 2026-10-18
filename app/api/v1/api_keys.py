"""Klucze API organizacji. Klucz jest pokazany raz (odpowiedz POST); w bazie leza prefiks i skrot SHA-256."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError, not_found
from app.api.v1.deps import OrgContext, org_access
from app.api.v1.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.control import scopes as sc
from app.control import service
from app.control.audit import record_event
from app.control.db import get_db
from app.control.models import ApiKey
from app.control.security import KEY_BRAND
from app.control.types import utcnow

router = APIRouter(tags=["api-keys"])

#: Gorny limit aktywnych kluczy organizacji (ochrona przed zasmieceniem bazy).
MAX_ACTIVE_KEYS = 50

_MANAGE = org_access(sc.KEYS_MANAGE)


def _out(key: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(id=key.id, name=key.name, prefix=f"{KEY_BRAND}{key.prefix}", scopes=list(key.scopes or []),
                     created_at=key.created_at, expires_at=key.expires_at, last_used_at=key.last_used_at,
                     revoked_at=key.revoked_at)


@router.get("/orgs/{org_id}/api-keys", response_model=list[ApiKeyOut], summary="List API keys",
            description="Metadata only: the key value is never returned after creation.")
async def list_keys(ctx: OrgContext = Depends(_MANAGE), db: AsyncSession = Depends(get_db)) -> list[ApiKeyOut]:
    rows = await db.execute(select(ApiKey).where(ApiKey.org_id == ctx.org_id).order_by(ApiKey.id.desc()))
    return [_out(k) for k in rows.scalars()]


@router.post(
    "/orgs/{org_id}/api-keys", status_code=201, response_model=ApiKeyCreated, summary="Create an API key",
    description="Returns the key once. Store it in a secret manager; only its hash is kept. A caller can grant "
                "only scopes it holds itself.",
    responses={403: {"description": "Requested scope exceeds the caller's own"}},
)
async def create_key(body: ApiKeyCreate, request: Request, ctx: OrgContext = Depends(_MANAGE),
                     db: AsyncSession = Depends(get_db)) -> ApiKeyCreated:
    excess = sorted(set(body.scopes) - ctx.scopes)
    if excess:
        raise ProblemError(403, "scope_escalation", "cannot grant scopes you do not hold",
                           extra={"scopes": excess})
    active = (await db.execute(select(func.count()).select_from(ApiKey).where(
        ApiKey.org_id == ctx.org_id, ApiKey.revoked_at.is_(None)))).scalar_one()
    if active >= MAX_ACTIVE_KEYS:
        raise ProblemError(409, "key_limit_reached", f"at most {MAX_ACTIVE_KEYS} active keys per organization")
    key, raw = await service.issue_api_key(
        db, org_id=ctx.org_id, name=body.name, scopes=body.scopes, created_by=ctx.principal.actor_id,
        expires_at=service.expiry_in_days(body.expires_in_days))
    await record_event(db, request, org_id=ctx.org_id, actor_type=ctx.principal.actor_type,
                       actor_id=ctx.principal.actor_id, action="key.created", target=f"key:{key.id}",
                       meta={"key_prefix": key.prefix, "scopes": key.scopes})
    await db.commit()
    return ApiKeyCreated(**_out(key).model_dump(), key=raw)


@router.delete("/orgs/{org_id}/api-keys/{key_id}", status_code=204, summary="Revoke an API key",
               description="Takes effect immediately. Revoking an already revoked key succeeds.")
async def revoke_key(key_id: str, request: Request, ctx: OrgContext = Depends(_MANAGE),
                     db: AsyncSession = Depends(get_db)) -> Response:
    key = (await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == ctx.org_id))).scalar_one_or_none()
    if key is None:
        raise not_found("API key")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        await record_event(db, request, org_id=ctx.org_id, actor_type=ctx.principal.actor_type,
                           actor_id=ctx.principal.actor_id, action="key.revoked", target=f"key:{key.id}",
                           meta={"key_prefix": key.prefix})
        await db.commit()
    return Response(status_code=204)
