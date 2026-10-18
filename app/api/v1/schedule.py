"""Harmonogram i powiadomienia organizacji: `GET/PUT /orgs/{org_id}/schedule`.

Adres webhooka czatu (`notify_webhook_url`) zawiera sekret: odpowiedz niesie tylko `notify_webhook_set` i maskowany
`notify_webhook_hint`, audyt `schedule.updated` tylko nazwe pola. `PUT` bez tego pola zostawia zapisany adres.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError
from app.api.v1.deps import OrgContext, org_access
from app.api.v1.schemas import ScheduleIn, ScheduleOut
from app.control import schedule as sched
from app.control import scopes as sc
from app.control.audit import record_event
from app.control.db import get_db
from app.control.models import OrgSchedule
from app.control.types import utcnow
from app.control.webhooks import UnsafeDestination, is_dev, validate_url

router = APIRouter(tags=["schedule"])

_READ = org_access(sc.ORG_READ)
_MANAGE = org_access(sc.ORG_MANAGE)

#: Komunikaty 422 dla odrzuconego adresu webhooka czatu (bez adresu: wartosc klienta nie jest odbijana).
_URL_MESSAGES = {
    "https_required": "the chat webhook URL must use https",
    "private_address": "the chat webhook URL must point to a public address",
    "credentials_in_url": "the chat webhook URL must not contain credentials",
    "url_malformed": "the chat webhook URL is malformed",
}


def schedule_out(row: OrgSchedule) -> ScheduleOut:
    return ScheduleOut(
        reminder_enabled=row.reminder_enabled, reminder_weekday=row.reminder_weekday, reminder_hour=row.reminder_hour,
        timezone=row.timezone, reminder_skip_if_recent_upload=row.reminder_skip_if_recent_upload,
        auto_run_on_upload=row.auto_run_on_upload, notify_on_finish=row.notify_on_finish,
        notify_webhook_kind=row.notify_webhook_kind or "slack", notify_webhook_set=bool(row.notify_webhook_url),
        notify_webhook_hint=sched.webhook_hint(row.notify_webhook_url),
        next_reminder_at=row.next_reminder_at, last_reminder_at=row.last_reminder_at, updated_at=row.updated_at)


def _checked_webhook_url(value: str | None, *, dev: bool) -> str | None:
    """Adres z `PUT` po walidacji SSRF (jak webhooki HMAC); `None` = usuniecie. Blad -> 422 z polem w `loc`."""
    if value is None:
        return None
    url = value.strip()
    try:
        validate_url(url, dev=dev)
    except UnsafeDestination as exc:
        raise ProblemError(422, "validation_error", "request validation failed", extra={"errors": [{
            "loc": ["body", "notify_webhook_url"],
            "msg": _URL_MESSAGES.get(exc.reason, "the chat webhook URL is not allowed"),
            "type": exc.reason}]}) from None
    return url


@router.get("/orgs/{org_id}/schedule", response_model=ScheduleOut, summary="Get the schedule and notifications",
            description="Weekly export reminder, auto-run after upload, the e-mail and the chat message when a run "
                        "finishes. An organization that never saved its schedule gets the defaults "
                        "(`updated_at: null`). The chat webhook address is never returned: `notify_webhook_set` "
                        "and the masked `notify_webhook_hint` say whether and which one is saved.")
async def get_schedule(ctx: OrgContext = Depends(_READ), db: AsyncSession = Depends(get_db)) -> ScheduleOut:
    row = await sched.get_schedule(db, ctx.org_id)
    return schedule_out(row if row is not None else sched.default_schedule(ctx.org_id))


@router.put("/orgs/{org_id}/schedule", response_model=ScheduleOut, summary="Save the schedule and notifications",
            description="Replaces the whole schedule (an omitted field takes its default), except the chat webhook "
                        "pair: `notify_webhook_url` omitted = unchanged, `null` = remove, text = set (https, public "
                        "address, no credentials); `notify_webhook_kind` omitted = unchanged. `reminder_weekday` 0-6 "
                        "(0 = Monday), `reminder_hour` 0-23 in "
                        "`timezone` (IANA name); an invalid value answers `422 validation_error` naming the field. "
                        "`next_reminder_at` is recomputed on every save.",
            responses={422: {"description": "Invalid weekday, hour, time zone or chat webhook address"}})
async def put_schedule(body: ScheduleIn, request: Request, ctx: OrgContext = Depends(_MANAGE),
                       db: AsyncSession = Depends(get_db)) -> ScheduleOut:
    url_sent = "notify_webhook_url" in body.model_fields_set
    url = _checked_webhook_url(body.notify_webhook_url, dev=is_dev(request.app.state.settings)) if url_sent else None
    now = utcnow()
    row = await sched.get_schedule(db, ctx.org_id)
    current = row if row is not None else sched.default_schedule(ctx.org_id)
    before = {field: getattr(current, field) for field in sched.EDITABLE_FIELDS}
    if row is None:
        row = current  # obiekt z wartosciami domyslnymi (`default_schedule`) staje sie wierszem
        db.add(row)
    # para webhooka czatu nie wraca do domyslnych przy pominieciu: adres jest tylko do zapisu, a rodzaj bez adresu
    # nie ma sensu zmieniac (klient API, ktory nie zna pary, nie przestawi Teams na format Slacka)
    untouched = {"notify_webhook_url"} | ({"notify_webhook_kind"} - body.model_fields_set)
    for field, value in body.model_dump(exclude=untouched).items():
        setattr(row, field, value)
    if url_sent:
        row.notify_webhook_url = url
    row.updated_at = now
    row.next_reminder_at = sched.next_for(row, now)
    # tylko nazwy pol: adres webhooka (sekret) nie trafia do audytu
    changed = sorted(f for f in sched.EDITABLE_FIELDS if before[f] != getattr(row, f))
    await record_event(db, request, org_id=ctx.org_id, actor_type=ctx.principal.actor_type,
                       actor_id=ctx.principal.actor_id, action="schedule.updated", target=f"org:{ctx.org_id}",
                       meta={"fields": changed})
    await db.commit()
    return schedule_out(row)
