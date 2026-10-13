"""Powiadomienie e-mail po zakonczeniu runu (`notify_on_finish` w harmonogramie organizacji).

Jedno miejsce wysylki: listener executora `run.finished` (ten sam mechanizm co webhooki), wiec kazdy run daje
najwyzej jedno powiadomienie. Wysylka idzie w tle (task asyncio) — nie blokuje zamka organizacji ani kolejnego runu.
Adresaci: czlonkowie organizacji. Tresc: pipeline, status (run `PARTIAL` jest nazwany czesciowym), okres danych,
SHA-256 raportu, kod bledu i link do widoku runu w kokpicie (bez tokenu). Zadnej tresci raportu ani danych klienta.
Audyt `schedule.notified`.
Zadania, ktore koncza sie bledem przed startem runu (np. brak pliku), nie emituja `run.finished` i nie sa zglaszane.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.control.audit import record_event
from app.control.executor import RunFinished
from app.control.mailer import Mailer, run_finished_mail
from app.control.models import Job, Org, OrgSchedule, RunRecord
from app.control.schedule import org_recipients, send_mails
from app.core.config import Settings

log = logging.getLogger(__name__)


class RunNotifier:
    """`on_run_finished` pasuje do `executor.Listener`."""

    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], mailer: Mailer) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.mailer = mailer
        self._tasks: set[asyncio.Task] = set()

    async def on_run_finished(self, event: RunFinished) -> None:
        async with self.sessionmaker() as db:
            schedule = await db.get(OrgSchedule, event.org_id)
        if schedule is None or not schedule.notify_on_finish:
            return
        task = asyncio.get_running_loop().create_task(self._notify(event), name=f"notify-{event.run_id}")
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
            period = (record.period_from.isoformat() if record and record.period_from else None,
                      record.period_to.isoformat() if record and record.period_to else None)
            mails = [run_finished_mail(
                self.settings, email, locale, org_name=org.name, pipeline=event.pipeline, succeeded=succeeded,
                run_id=event.run_id, period=period, report_sha256=record.sha256_html if record else None,
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
