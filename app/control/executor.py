"""Wykonawca zadan in-process (`Job`): kolejka -> run w watku -> `RunRecord`, uzycie, audyt, webhook.

Gwarancje:
    * w obrebie organizacji w danej chwili dziala jeden run (zamek `TenantRegistry.lock`),
    * globalnie najwyzej `DAAS_MAX_CONCURRENT_RUNS` runow naraz (semafor),
    * stan jest w control DB, nie w pamieci: po restarcie `running` -> `failed` (`abandoned`), a zadania `queued`
      sa ponownie kolejkowane,
    * zadanie `cancelled` w kolejce nie startuje (warunkowy UPDATE przy przejsciu `queued` -> `running`).
Jeden proces, `workers=1`: kolejka i semafory zyja w tym procesie.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.control import service
from app.control.audit import record_event
from app.control.models import Job, Org, RunRecord, Upload
from app.control.tenancy import TenantRegistry
from app.control.types import utcnow
from app.core.config import Settings
from app.core.models import RunRequest, RunStatus
from app.core.redact import error_code_of

log = logging.getLogger(__name__)

TERMINAL = ("succeeded", "failed", "cancelled")


@dataclass(frozen=True)
class RunFinished:
    """Zdarzenie dla nasluchujacych (webhooki). Bez tresci raportu i danych klienta."""

    org_id: str
    job_id: str
    run_id: str
    pipeline: str
    status: str  # succeeded | failed
    run_status: str  # SUCCESS | PARTIAL | FAILED
    finished_at_iso: str


Listener = Callable[[RunFinished], Awaitable[None]]


class StreamSlot:
    """Miejsce w limicie strumieni SSE; `release()` jest idempotentne (generator i zadanie w tle wolaja je oba)."""

    def __init__(self, bus: JobBus, key: str) -> None:
        self._bus, self._key, self._released = bus, key, False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._bus._release(self._key)


class JobBus:
    """Powiadomienia o zmianie zadania dla SSE: `wait` budzi sie od razu po `publish` (albo po timeout).

    Prowadzi tez ewidencje otwartych strumieni (limity per podmiot i globalny) oraz flage zamkniecia serwera:
    `shutdown()` budzi wszystkie strumienie, ktore wtedy koncza sie same."""

    def __init__(self) -> None:
        self._events: dict[str, set[asyncio.Event]] = {}
        self._streams: dict[str, int] = {}
        self.closing = False

    def subscribe(self, job_id: str) -> asyncio.Event:
        event = asyncio.Event()
        self._events.setdefault(job_id, set()).add(event)
        return event

    def unsubscribe(self, job_id: str, event: asyncio.Event) -> None:
        listeners = self._events.get(job_id)
        if listeners:
            listeners.discard(event)
            if not listeners:
                self._events.pop(job_id, None)

    def publish(self, job_id: str) -> None:
        for event in list(self._events.get(job_id, ())):
            event.set()

    # ------------------------------------------------------------------ strumienie SSE
    @property
    def active_streams(self) -> int:
        return sum(self._streams.values())

    def open_stream(self, key: str, *, per_key: int, total: int) -> StreamSlot | None:
        """Rezerwuje miejsce na strumien dla podmiotu `key`; None, gdy limit podmiotu albo globalny jest wyczerpany."""
        if self._streams.get(key, 0) >= per_key or self.active_streams >= total:
            return None
        self._streams[key] = self._streams.get(key, 0) + 1
        return StreamSlot(self, key)

    def _release(self, key: str) -> None:
        left = self._streams.get(key, 0) - 1
        if left > 0:
            self._streams[key] = left
        else:
            self._streams.pop(key, None)

    def shutdown(self) -> None:
        """Serwer sie zamyka: nie przyjmujemy nowych strumieni, istniejace sie koncza (budzimy je)."""
        self.closing = True
        for listeners in list(self._events.values()):
            for event in list(listeners):
                event.set()


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def effective_llm(settings: Settings, org: Org) -> bool:
    """Streszczenie LLM: w cloud wylacznie przy opt-in organizacji; w selfhost takze przy fladze wdrozenia."""
    return bool(org.llm_narrative or (settings.daas_llm_narrative and not settings.cloud))


class JobExecutor:
    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker[AsyncSession],
                 registry: TenantRegistry) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.registry = registry
        self.bus = JobBus()
        self.listeners: list[Listener] = []
        self._global: asyncio.Semaphore | None = None
        self._tasks: set[asyncio.Task] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.running_now = 0
        self.max_running_seen = 0

    # ------------------------------------------------------------------ cykl zycia
    async def start(self) -> None:
        """Ustawia semafor i odzyskuje stan po restarcie: `running` -> `failed:abandoned`, `queued` -> kolejka."""
        self._loop = asyncio.get_running_loop()
        self._global = asyncio.Semaphore(self.settings.daas_max_concurrent_runs)
        async with self.sessionmaker() as db:
            abandoned = await db.execute(update(Job).where(Job.status == "running").values(
                status="failed", error_code="abandoned", message="interrupted by restart",
                finished_at=utcnow()))
            queued = (await db.execute(select(Job.id, Job.org_id).where(Job.status == "queued")
                                       .order_by(Job.id))).all()
            await db.commit()
        if abandoned.rowcount:
            log.warning("marked %d interrupted job(s) as failed (abandoned)", abandoned.rowcount)
        for job_id, org_id in queued:
            self.submit(job_id, org_id)

    async def stop(self) -> None:
        """Anuluje oczekujace taski. Trwajacy w watku run nie da sie przerwac; jego `running` zostanie po
        restarcie oznaczone jako `abandoned`."""
        self.bus.shutdown()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # ------------------------------------------------------------------ kolejka
    def submit(self, job_id: str, org_id: str) -> None:
        task = asyncio.get_running_loop().create_task(self._run(job_id, org_id), name=f"job-{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job_id: str, org_id: str) -> None:
        if self._global is None:
            raise RuntimeError("executor not started")
        try:
            async with self.registry.lock(org_id), self._global:
                await self._execute(job_id, org_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("job crashed job_id=%s", job_id)
            await self._finish(job_id, status="failed", error_code="internal_error", message="internal error")

    # ------------------------------------------------------------------ wykonanie
    async def _execute(self, job_id: str, org_id: str) -> None:
        async with self.sessionmaker() as db:
            claimed = await db.execute(update(Job).where(Job.id == job_id, Job.status == "queued").values(
                status="running", started_at=utcnow(), progress=5, message="starting"))
            await db.commit()
            if claimed.rowcount != 1:
                return  # anulowane (albo juz wzięte) zanim przyszla kolej
            job = await db.get(Job, job_id)
            org = await db.get(Org, org_id)
            upload = await db.get(Upload, job.upload_id) if job.upload_id else None
        self.bus.publish(job_id)

        source_path: str | None = None
        if upload is not None:
            paths = self.registry.paths(org_id)
            candidate = paths.uploads / upload.path
            if not paths.contains(candidate) or not candidate.is_file():
                await self._finish(job_id, status="failed", error_code="SOURCE_UNAVAILABLE",
                                   message="uploaded file is no longer available")
                return
            source_path = str(candidate)

        if self.settings.cloud and source_path is None and not job.force_mock:
            # M-7: np. upload usuniety przez retencje (upload_id wyzerowane) — nigdy nie siegamy po zrodla z env
            await self._finish(job_id, status="failed", error_code="SOURCE_UNAVAILABLE",
                               message="no source file for this job")
            return

        runner = await self.registry.aget(org_id, llm_narrative=effective_llm(self.settings, org))
        request = RunRequest(pipeline=job.pipeline, force_mock=job.force_mock, client_name=job.client_name,
                             source_path=source_path)
        loop = asyncio.get_running_loop()

        def on_progress(percent: int, message: str) -> None:  # wolane z watku runu
            asyncio.run_coroutine_threadsafe(self._progress(job_id, percent, message), loop)

        self.registry.mark_in_use(org_id)
        self.running_now += 1
        self.max_running_seen = max(self.max_running_seen, self.running_now)
        try:
            result = await run_in_threadpool(runner.run, request, on_progress)
            hashes = await run_in_threadpool(self._hash_reports, result)
            period = (None, None) if result.run_status == RunStatus.FAILED else await run_in_threadpool(
                self._run_period, runner.db, job.pipeline, result.run_id)
        finally:
            self.running_now -= 1
            self.registry.release(org_id)

        run_status = result.run_status.value
        failed = result.run_status == RunStatus.FAILED
        error_code = error_code_of(result.error) if failed else None
        # C9: PARTIAL bez rekordow (zrodlo bez danych) to nie jest uslugowy sukces: nie zuzywa limitu planu,
        # a job konczy sie `failed/no_records` (SSE i cockpit widza ten sam stan). RunRecord zostaje w historii.
        no_records = not failed and not result.records_processed
        job_failed = failed or no_records
        if no_records:
            error_code = "no_records"
        async with self.sessionmaker() as db:
            db.add(RunRecord(run_id=result.run_id, org_id=org_id, pipeline=job.pipeline, status=run_status,
                             job_id=job_id, sha256_html=hashes.get("html"), sha256_md=hashes.get("md"),
                             sha256_json=hashes.get("json"), period_from=period[0], period_to=period[1]))
            if not job_failed:
                await service.add_usage(db, org_id, runs=1)
            await db.execute(update(Job).where(Job.id == job_id).values(
                status="failed" if job_failed else "succeeded", progress=100, run_id=result.run_id,
                error_code=error_code, finished_at=utcnow(),
                message="run failed" if failed else ("no records in the source" if no_records else "done")))
            await record_event(db, None, org_id=org_id, actor_type="system", actor_id=None, action="run.finished",
                               target=f"run:{result.run_id}", settings=self.settings,
                               meta={"pipeline": job.pipeline, "run_id": result.run_id, "job_id": job_id,
                                     "status": run_status})
            await db.commit()
        self.bus.publish(job_id)
        event = RunFinished(org_id=org_id, job_id=job_id, run_id=result.run_id, pipeline=job.pipeline,
                            status="failed" if job_failed else "succeeded", run_status=run_status,
                            finished_at_iso=utcnow().isoformat())
        for listener in self.listeners:
            try:
                await listener(event)
            except Exception:  # noqa: BLE001
                log.exception("run.finished listener failed job_id=%s", job_id)

    @staticmethod
    def _run_period(db, pipeline: str, run_id: str) -> tuple[date | None, date | None]:
        """Zakres dat w danych runu z bazy najemcy: ecommerce_demo = min/max daty zamowienia, uksc_evidence = dzien
        zbioru paczki (`collected_at_utc`), cs2_demo = brak. Blad odczytu nie psuje runu (jest logowany)."""
        sql = {
            "ecommerce_demo": "SELECT min(order_date), max(order_date) FROM ecommerce_orders WHERE run_id = ?",
            "uksc_evidence": "SELECT CAST(min(collected_at_utc) AS DATE), CAST(max(collected_at_utc) AS DATE) "
                             "FROM uksc_hosts WHERE run_id = ?",
        }.get(pipeline)
        if sql is None:
            return None, None
        try:
            with db.connect() as con:
                first, last = con.execute(sql, [run_id]).fetchone()
        except Exception:  # noqa: BLE001
            log.exception("could not read the data period run_id=%s", run_id)
            return None, None
        return first, last

    @staticmethod
    def _hash_reports(result) -> dict[str, str]:
        out: dict[str, str] = {}
        for fmt, path in (("html", result.report_html_path), ("md", result.report_path),
                          ("json", result.report_json_path)):
            digest = sha256_file(Path(path)) if path else None
            if digest:
                out[fmt] = digest
        return out

    # ------------------------------------------------------------------ aktualizacje stanu
    async def _progress(self, job_id: str, percent: int, message: str) -> None:
        """Postep nie cofa sie i nie nadpisuje stanu koncowego (warunek w UPDATE)."""
        async with self.sessionmaker() as db:
            await db.execute(update(Job).where(
                Job.id == job_id, Job.status == "running", Job.progress < percent,
            ).values(progress=percent, message=message[:200]))
            await db.commit()
        self.bus.publish(job_id)

    async def _finish(self, job_id: str, *, status: str, error_code: str | None, message: str) -> None:
        async with self.sessionmaker() as db:
            await db.execute(update(Job).where(Job.id == job_id, Job.status.in_(("queued", "running"))).values(
                status=status, error_code=error_code, message=message[:200], progress=100,
                finished_at=utcnow()))
            await db.commit()
        self.bus.publish(job_id)
