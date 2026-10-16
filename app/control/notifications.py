"""Powiadomienia po zakonczeniu runu: e-mail (`notify_on_finish`) i wiadomosc na czacie zespolu (`notify_webhook_url`).

Jedno miejsce wysylki: listener executora `run.finished` (ten sam mechanizm co webhooki), wiec kazdy run daje
najwyzej jeden mail na adresata i jeden POST na czat. Oba kanaly sa niezalezne (mozna wlaczyc jeden albo oba; blad
jednego nie blokuje drugiego ani runu) i ida w tle (task asyncio) — nie blokuja zamka organizacji ani kolejnego runu.
Tresc: pipeline, status (run `PARTIAL` jest nazwany czesciowym), okres danych, SHA-256 raportu, kod bledu i link do
widoku runu w kokpicie (bez tokenu). Zadnej tresci raportu ani danych klienta.

* e-mail: adresaci = czlonkowie organizacji; audyt `schedule.notified`,
* czat: payload wg `notify_webhook_kind` (`app/control/chat_messages.py`), jedna proba `POST` z ochrona SSRF jak
  webhooki HMAC (`post_json`: adres publiczny, pinning IP, bez przekierowan), timeout
  `DAAS_NOTIFY_WEBHOOK_TIMEOUT_SEC`; sukces = 2xx. Adres zawiera sekret: nie trafia do logow ani audytu, tresc
  odpowiedzi nie jest czytana. Audyt `schedule.webhook_notified` (`channel`, `sent`, `status_code`, `error_type`).
Zadania, ktore koncza sie bledem przed startem runu (np. brak pliku), nie emituja `run.finished` i nie sa zglaszane.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.control.audit import record_event
from app.control.chat_messages import DEFAULT_KIND, build_payload, encode, run_message
from app.control.executor import RunFinished
from app.control.mailer import Mailer, run_finished_mail
from app.control.models import Job, Org, OrgSchedule, RunRecord
from app.control.schedule import org_recipients, send_mails
from app.control.webhooks import USER_AGENT, Resolver, is_dev, post_json, system_resolver
from app.core.config import Settings

log = logging.getLogger(__name__)


def _period(record: RunRecord | None) -> tuple[str | None, str | None]:
    return (record.period_from.isoformat() if record and record.period_from else None,
            record.period_to.isoformat() if record and record.period_to else None)


class RunNotifier:
    """`on_run_finished` pasuje do `executor.Listener`. `transport`/`resolver` podstawiaja testy (jak w
    `WebhookDispatcher`); produkcyjnie `None` = prawdziwa siec i `getaddrinfo`."""

    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], mailer: Mailer, *,
                 transport: httpx.AsyncBaseTransport | None = None, resolver: Resolver = system_resolver) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.mailer = mailer
        self.transport = transport
        self.resolver = resolver
        self._tasks: set[asyncio.Task] = set()

    async def on_run_finished(self, event: RunFinished) -> None:
        async with self.sessionmaker() as db:
            schedule = await db.get(OrgSchedule, event.org_id)
        if schedule is None:
            return
        if schedule.notify_on_finish:
            self._spawn(self._notify(event), f"notify-{event.run_id}")
        if schedule.notify_webhook_url:
            self._spawn(self._notify_chat(event, schedule.notify_webhook_url,
                                          schedule.notify_webhook_kind or DEFAULT_KIND), f"notify-chat-{event.run_id}")

    def _spawn(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def drain(self, timeout: float = 30.0) -> None:
        """Czeka na wysylki w toku (testy)."""
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=timeout)

    async def _notify(self, event: RunFinished) -> None:
        try:
            async with self.sessionmaker() as db:
                org = await db.get(Org, event.org_id)
                record = await db.get(RunRecord, event.run_id)
                job = await db.get(Job, event.job_id)
                recipients = await org_recipients(db, event.org_id) if org is not None else []
            if org is None:
                return
            succeeded = event.status == "succeeded"
            mails = [run_finished_mail(
                self.settings, email, locale, org_name=org.name, pipeline=event.pipeline, succeeded=succeeded,
                run_id=event.run_id, period=_period(record), report_sha256=record.sha256_html if record else None,
                error_code=job.error_code if job is not None else None,
                partial=event.run_status == "PARTIAL") for email, locale in recipients]
            sent = await send_mails(self.mailer, mails)
            async with self.sessionmaker() as db:
                await record_event(db, None, org_id=event.org_id, actor_type="system", actor_id=None,
                                   action="schedule.notified", target=f"run:{event.run_id}", settings=self.settings,
                                   meta={"run_id": event.run_id, "job_id": event.job_id, "status": event.status,
                                         "sent": sent > 0, "recipients": sent})
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("run notification failed run_id=%s", event.run_id)

    async def _notify_chat(self, event: RunFinished, url: str, kind: str) -> None:
        """Jedna proba wysylki na czat; wynik (bez adresu) w audycie `schedule.webhook_notified`."""
        try:
            async with self.sessionmaker() as db:
                org = await db.get(Org, event.org_id)
                record = await db.get(RunRecord, event.run_id)
                job = await db.get(Job, event.job_id)
            if org is None:
                return
            message = run_message(self.settings, event, org_name=org.name, period=_period(record),
                                  report_sha256=record.sha256_html if record else None,
                                  error_code=job.error_code if job is not None else None)
            body = encode(build_payload(kind, message))
            status, error_type = await post_json(
                url, body, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                dev=is_dev(self.settings), timeout=self.settings.daas_notify_webhook_timeout_sec,
                transport=self.transport, resolver=self.resolver)
            sent = status is not None and 200 <= status < 300
            if not sent and error_type is None:
                error_type = "http_status"
            log.info("chat notification %s run_id=%s kind=%s status=%s error=%s",
                     "sent" if sent else "failed", event.run_id, kind, status, error_type)
            meta: dict[str, Any] = {"run_id": event.run_id, "job_id": event.job_id, "status": event.run_status,
                                    "channel": kind, "sent": sent}
            if status is not None:
                meta["status_code"] = status
            if error_type is not None:
                meta["error_type"] = error_type
            async with self.sessionmaker() as db:
                await record_event(db, None, org_id=event.org_id, actor_type="system", actor_id=None,
                                   action="schedule.webhook_notified", target=f"run:{event.run_id}",
                                   settings=self.settings, meta=meta)
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("chat notification failed run_id=%s", event.run_id)
