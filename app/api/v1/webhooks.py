"""Webhooki organizacji: lista, tworzenie (sekret pokazany raz), usuniecie i ping.

Dostarczanie `run.finished` zyje w `app/control/webhooks.py` (listener executora). Sekret podpisu jest wyliczany
z `DAAS_SECRET_KEY` i id webhooka; w bazie jest tylko jego skrot SHA-256 (jak klucze API).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError, not_found
from app.api.v1.deps import OrgContext, org_access
from app.api.v1.schemas import WebhookCreate, WebhookCreated, WebhookOut, WebhookPingOut
from app.control import scopes as sc
from app.control.audit import record_event
from app.control.db import get_db
from app.control.ids import new_ulid
from app.control.models import Webhook
from app.control.security import sha256_hex, webhook_signing_secret
from app.control.webhooks import EVENT_PING, UnsafeDestination, build_body, is_dev, validate_url

router = APIRouter(tags=["webhooks"])

#: Najwiecej webhookow jednej organizacji (ochrona przed zasmieceniem i amplifikacja dostaw).
MAX_WEBHOOKS = 10

_MANAGE = org_access(sc.WEBHOOKS_MANAGE)

_URL_MESSAGES = {
    "https_required": "the webhook URL must use https",
    "private_address": "the webhook URL must point to a public address",
    "credentials_in_url": "the webhook URL must not contain credentials",
    "url_malformed": "the webhook URL is malformed",
}


def _out(hook: Webhook) -> WebhookOut:
    return WebhookOut(id=hook.id, url=hook.url, events=list(hook.events or []), active=hook.active,
                      created_at=hook.created_at)


async def _load(db: AsyncSession, org_id: str, webhook_id: str) -> Webhook:
    hook = (await db.execute(select(Webhook).where(Webhook.id == webhook_id, Webhook.org_id == org_id))
            ).scalar_one_or_none()
    if hook is None:
        raise not_found("webhook")
    return hook


@router.get("/orgs/{org_id}/webhooks", response_model=list[WebhookOut], summary="List webhooks",
            description="The signing secret is never returned after creation.")
async def list_webhooks(ctx: OrgContext = Depends(_MANAGE), db: AsyncSession = Depends(get_db)) -> list[WebhookOut]:
    rows = await db.execute(select(Webhook).where(Webhook.org_id == ctx.org_id).order_by(Webhook.id))
    return [_out(h) for h in rows.scalars()]


@router.post(
    "/orgs/{org_id}/webhooks", status_code=201, response_model=WebhookCreated, summary="Create a webhook",
    description="Registers an HTTPS endpoint for `run.finished`. The response carries the signing secret once; "
                "every delivery has `X-DaaS-Signature: sha256=<HMAC-SHA256(secret, body)>`, `X-DaaS-Event`, "
                "`X-DaaS-Delivery` and `X-DaaS-Timestamp`. Non-public addresses are refused outside dev. A delivery "
                "is attempted up to 3 times (back-off from `DAAS_WEBHOOK_BACKOFF_SEC`), redirects are not followed.",
    responses={409: {"description": "Duplicate URL or webhook limit reached"},
               422: {"description": "URL not allowed"}, 503: {"description": "Webhooks need DAAS_SECRET_KEY"}},
)
async def create_webhook(body: WebhookCreate, request: Request, ctx: OrgContext = Depends(_MANAGE),
                         db: AsyncSession = Depends(get_db)) -> WebhookCreated:
    settings = request.app.state.settings
    if not settings.daas_secret_key:
        raise ProblemError(503, "webhooks_unavailable", "webhooks need DAAS_SECRET_KEY to be configured")
    events = sorted(set(body.events))
    if not events:
        raise ProblemError(422, "events_required", "at least one event is required")
    url = body.url.strip()
    try:
        validate_url(url, dev=is_dev(settings))
    except UnsafeDestination as exc:
        raise ProblemError(422, "webhook_url_invalid", _URL_MESSAGES.get(exc.reason, "the webhook URL is not allowed"),
                           extra={"reason_code": exc.reason}) from None
    count = (await db.execute(select(func.count()).select_from(Webhook).where(
        Webhook.org_id == ctx.org_id))).scalar_one()
    if count >= MAX_WEBHOOKS:
        raise ProblemError(409, "webhook_limit_reached", f"at most {MAX_WEBHOOKS} webhooks per organization")
    webhook_id = new_ulid()
    secret = webhook_signing_secret(webhook_id, settings.daas_secret_key)
    hook = Webhook(id=webhook_id, org_id=ctx.org_id, url=url, secret_hash=sha256_hex(secret), events=events,
                   active=True)
    db.add(hook)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ProblemError(409, "webhook_exists", "a webhook with this URL already exists") from None
    await record_event(db, request, org_id=ctx.org_id, actor_type=ctx.principal.actor_type,
                       actor_id=ctx.principal.actor_id, action="webhook.created", target=f"webhook:{hook.id}",
                       meta={"webhook_id": hook.id})
    await db.commit()
    await db.refresh(hook)
    return WebhookCreated(**_out(hook).model_dump(), secret=secret)


@router.delete("/orgs/{org_id}/webhooks/{webhook_id}", status_code=204, summary="Delete a webhook",
               responses={404: {"description": "Webhook not found"}})
async def delete_webhook(webhook_id: str, request: Request, ctx: OrgContext = Depends(_MANAGE),
                         db: AsyncSession = Depends(get_db)) -> Response:
    hook = await _load(db, ctx.org_id, webhook_id)
    await db.delete(hook)
    await record_event(db, request, org_id=ctx.org_id, actor_type=ctx.principal.actor_type,
                       actor_id=ctx.principal.actor_id, action="webhook.deleted", target=f"webhook:{webhook_id}",
                       meta={"webhook_id": webhook_id})
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/orgs/{org_id}/webhooks/{webhook_id}/ping", response_model=WebhookPingOut, summary="Send a test event",
    description="Sends one signed `ping` event (single attempt) and reports whether the endpoint answered 2xx and the "
                "class of its answer (`status_class`); the exact status code is not returned.",
    responses={404: {"description": "Webhook not found"},
               503: {"description": "Webhook delivery is paused for maintenance"}},
)
async def ping_webhook(webhook_id: str, request: Request, ctx: OrgContext = Depends(_MANAGE),
                       db: AsyncSession = Depends(get_db)) -> WebhookPingOut:
    if request.app.state.settings.daas_maintenance_mode:
        raise ProblemError(503, "maintenance", "webhook delivery is paused for maintenance")
    hook = await _load(db, ctx.org_id, webhook_id)
    dispatcher = request.app.state.webhooks
    result = await dispatcher.deliver(hook, EVENT_PING, build_body(EVENT_PING, {"org_id": ctx.org_id}),
                                      max_attempts=1)
    await dispatcher.audit_result(request, hook, EVENT_PING, result, actor_type=ctx.principal.actor_type,
                                  actor_id=ctx.principal.actor_id)
    code = result.status_code
    klass = f"{code // 100}xx" if code is not None and 200 <= code < 600 else "error"
    return WebhookPingOut(delivered=result.delivered, status_class=klass)
