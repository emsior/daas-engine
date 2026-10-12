"""Harmonogram i powiadomienia organizacji: `GET/PUT /orgs/{org_id}/schedule`."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import OrgContext, org_access
from app.api.v1.schemas import ScheduleIn, ScheduleOut
from app.control import schedule as sched
from app.control import scopes as sc
from app.control.audit import record_event
from app.control.db import get_db
from app.control.models import OrgSchedule
from app.control.types import utcnow

router = APIRouter(tags=["schedule"])

_READ = org_access(sc.ORG_READ)
_MANAGE = org_access(sc.ORG_MANAGE)


def schedule_out(row: OrgSchedule) -> ScheduleOut:
    return ScheduleOut(
        reminder_enabled=row.reminder_enabled, reminder_weekday=row.reminder_weekday, reminder_hour=row.reminder_hour,
        timezone=row.timezone, reminder_skip_if_recent_upload=row.reminder_skip_if_recent_upload,
        auto_run_on_upload=row.auto_run_on_upload, notify_on_finish=row.notify_on_finish,
        next_reminder_at=row.next_reminder_at, last_reminder_at=row.last_reminder_at, updated_at=row.updated_at)


@router.get("/orgs/{org_id}/schedule", response_model=ScheduleOut, summary="Get the schedule and notifications",
            description="Weekly export reminder, auto-run after upload and the e-mail when a run finishes. "
                        "An organization that never saved its schedule gets the defaults (`updated_at: null`).")
async def get_schedule(ctx: OrgContext = Depends(_READ), db: AsyncSession = Depends(get_db)) -> ScheduleOut:
    row = await sched.get_schedule(db, ctx.org_id)
    return schedule_out(row if row is not None else sched.default_schedule(ctx.org_id))


@router.put("/orgs/{org_id}/schedule", response_model=ScheduleOut, summary="Save the schedule and notifications",
            description="Replaces the whole schedule (an omitted field takes its default). `reminder_weekday` 0-6 "
                        "(0 = Monday), `reminder_hour` 0-23 in `timezone` (IANA name); an invalid value answers "
                        "`422 validation_error` naming the field. `next_reminder_at` is recomputed on every save.",
            responses={422: {"description": "Invalid weekday, hour or time zone"}})
async def put_schedule(body: ScheduleIn, request: Request, ctx: OrgContext = Depends(_MANAGE),
                       db: AsyncSession = Depends(get_db)) -> ScheduleOut:
    now = utcnow()
    row = await sched.get_schedule(db, ctx.org_id)
    before = schedule_out(row if row is not None else sched.default_schedule(ctx.org_id)).model_dump()
    if row is None:
        row = OrgSchedule(org_id=ctx.org_id)
        db.add(row)
    values = body.model_dump()
    for field, value in values.items():
        setattr(row, field, value)
    row.updated_at = now
    row.next_reminder_at = sched.next_for(row, now)
    changed = sorted(f for f in sched.EDITABLE_FIELDS if before[f] != values[f])
    await record_event(db, request, org_id=ctx.org_id, actor_type=ctx.principal.actor_type,
                       actor_id=ctx.principal.actor_id, action="schedule.updated", target=f"org:{ctx.org_id}",
                       meta={"fields": changed})
    await db.commit()
    return schedule_out(row)
