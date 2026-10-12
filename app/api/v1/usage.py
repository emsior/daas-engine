"""Zuzycie organizacji w biezacym i poprzednim miesiacu oraz limity planu."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import OrgContext, org_access
from app.api.v1.schemas import PlanLimitsOut, UsagePeriod, UsageReport
from app.control import scopes as sc
from app.control import service
from app.control.db import get_db
from app.control.models import Org, UsageCounter
from app.control.plans import limits_for, previous_period

router = APIRouter(tags=["usage"])

_READ = org_access(sc.USAGE_READ)


@router.get(
    "/orgs/{org_id}/usage", response_model=UsageReport, summary="Usage and plan limits",
    description="Runs and uploaded bytes for the current calendar month (UTC) and the previous one, the limits of "
                "the organization's plan (`null` = unlimited) and its upload retention. When a limit is reached, "
                "`POST .../jobs` and `POST .../uploads` answer `429` with code `plan_limit`.",
)
async def get_usage(ctx: OrgContext = Depends(_READ), db: AsyncSession = Depends(get_db)) -> UsageReport:
    return await usage_report(db, ctx.org)


async def usage_report(db: AsyncSession, org: Org) -> UsageReport:
    """Zuzycie organizacji (biezacy i poprzedni miesiac UTC) i limity jej planu — logika wspolna v1 i /mcp."""
    current = service.current_period()
    previous = previous_period(current)
    rows = (await db.execute(select(UsageCounter).where(
        UsageCounter.org_id == org.id, UsageCounter.period.in_((current, previous))))).scalars().all()
    by_period = {row.period: row for row in rows}

    def period(name: str) -> UsagePeriod:
        row = by_period.get(name)
        return UsagePeriod(period=name, runs=row.runs if row else 0, upload_bytes=row.upload_bytes if row else 0)

    limits = limits_for(org.plan)
    return UsageReport(
        org_id=org.id, plan=org.plan, retention_days=org.retention_days,
        current=period(current), previous=period(previous),
        limits=PlanLimitsOut(runs_per_month=limits.runs_per_month,
                             upload_bytes_per_month=limits.upload_bytes_per_month))
