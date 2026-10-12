"""Egzekwowanie limitow planu przy `POST /jobs` i `POST /uploads` (429 `plan_limit`).

Zaleznosc jest podpieta na poziomie `include_router` w `app/api/v1/__init__.py` (handlery tras nie wiedza o planach).
Dziala wylacznie na dwoch trasach z `GUARDED`; pozostale trasy tych routerow przechodza bez zmian.
Kolejnosc wobec autoryzacji trasy: wolajacy bez dostepu do organizacji albo bez wymaganego zakresu NIE dostaje
429 — guard odpuszcza i pozwala trasie zwrocic 404/403 (limit nie moze maskowac bledow autoryzacji).
"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends, Path, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError
from app.api.v1.deps import Principal, get_principal
from app.control import scopes as sc
from app.control import service
from app.control.audit import record_event
from app.control.db import get_db
from app.control.models import Job, Membership, Org, UsageCounter
from app.control.plans import limits_for

#: (metoda, szablon trasy) -> wymagany zakres. Tylko te dwie trasy podlegaja limitom planu.
#: Szablon jest wzgledem routera (`route.path` nie zawiera prefiksu `/api/v1`).
GUARDED: dict[tuple[str, str], str] = {
    ("POST", "/orgs/{org_id}/jobs"): sc.RUNS_WRITE,
    ("POST", "/orgs/{org_id}/uploads"): sc.UPLOADS_WRITE,
}


async def _scopes_in_org(db: AsyncSession, principal: Principal, org_id: str) -> frozenset[str] | None:
    """Zakresy wolajacego w organizacji albo None, gdy nie ma do niej dostepu."""
    if principal.user is not None:
        membership = await db.get(Membership, (org_id, principal.user.id))
        return sc.ROLE_SCOPES.get(membership.role, frozenset()) if membership else None
    return principal.scopes if principal.org_id == org_id else None


async def plan_guard(request: Request, org_id: str = Path(...), principal: Principal = Depends(get_principal),
                     db: AsyncSession = Depends(get_db)) -> None:
    route = request.scope.get("route")
    template = getattr(route, "path", "").removeprefix("/api/v1")  # odporne na wersje FastAPI z/bez prefiksu
    required = GUARDED.get((request.method, template))
    if required is None:
        return
    scopes = await _scopes_in_org(db, principal, org_id)
    org = await db.get(Org, org_id) if scopes is not None else None
    if org is None or required not in scopes:
        return  # trasa sama odpowie 404/403
    if request.url.path.endswith("/jobs"):
        await check_plan_limit(db, request, principal=principal, org=org, resource="runs")
    elif request.url.path.endswith("/uploads"):
        await check_plan_limit(db, request, principal=principal, org=org, resource="upload_bytes",
                               declared_bytes=_content_length(request))


async def check_plan_limit(db: AsyncSession, request: Request, *, principal: Principal, org: Org,
                           resource: Literal["runs", "upload_bytes"], declared_bytes: int = 0,
                           target: str | None = None) -> None:
    """429 `plan_limit` (z audytem `auth_denied`), gdy miesieczny limit planu jest wyczerpany — wspolne dla v1 i /mcp.

    `runs`: zuzyte runy + zadania w toku >= limit. `upload_bytes`: zuzyte bajty + `declared_bytes` >= limit."""
    org_id = org.id
    limits = limits_for(org.plan)
    counter = await db.get(UsageCounter, (org_id, service.current_period()))
    runs, uploaded = (counter.runs, counter.upload_bytes) if counter else (0, 0)

    limit, used = None, 0
    if resource == "runs" and limits.runs_per_month is not None:
        # zadania w toku tez zajmuja miejsce w limicie: licznik rosnie dopiero po zakonczeniu runu
        pending = (await db.execute(select(func.count()).select_from(Job).where(
            Job.org_id == org_id, Job.status.in_(("queued", "running"))))).scalar_one()
        if runs + pending >= limits.runs_per_month:
            limit, used = limits.runs_per_month, runs + pending
    elif resource == "upload_bytes" and limits.upload_bytes_per_month is not None:
        if uploaded + declared_bytes >= limits.upload_bytes_per_month:
            limit, used = limits.upload_bytes_per_month, uploaded
    if limit is None:
        return
    await record_event(db, request, org_id=org_id, actor_type=principal.actor_type, actor_id=principal.actor_id,
                       action="auth_denied", target=(target or request.url.path)[:80],
                       meta={"reason": "plan_limit", "plan": org.plan})
    await db.commit()
    raise ProblemError(429, "plan_limit", f"monthly {resource} limit of the {org.plan} plan reached",
                       extra={"plan": org.plan, "resource": resource, "limit": limit, "used": used,
                              "period": service.current_period()})


def _content_length(request: Request) -> int:
    """Zadeklarowany rozmiar zadania (naglowek); brak/blad = 0, wtedy liczy sie tylko juz zuzyta kwota."""
    try:
        return max(0, int(request.headers.get("content-length", "0")))
    except ValueError:
        return 0
