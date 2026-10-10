"""Zadania asynchroniczne: `POST /jobs` (202), lista, szczegoly, anulowanie i strumien zdarzen SSE."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.api.problems import ProblemError, not_found
from app.api.v1.deps import OrgContext, org_access
from app.api.v1.schemas import JobCreate, JobOut, JobPage, JobStatus
from app.control import scopes as sc
from app.control.audit import record_event
from app.control.db import get_db
from app.control.executor import TERMINAL, StreamSlot
from app.control.models import Job, Upload
from app.control.types import utcnow
from app.core.ratelimit_v1 import enforce, principal_key

log = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])

#: Najwiecej zadan oczekujacych lub trwajacych w jednej organizacji (ochrona kolejki).
MAX_PENDING_JOBS = 20

_READ = org_access(sc.RUNS_READ)
_WRITE = org_access(sc.RUNS_WRITE)

#: Rodzaj uploadu akceptowany przez pipeline. `None` = pipeline nie czyta pliku klienta.
PIPELINE_UPLOAD_KINDS: dict[str, tuple[str, ...] | None] = {
    "ecommerce_demo": ("csv", "xlsx"),
    "uksc_evidence": ("package",),
    "cs2_demo": None,
}


def job_out(job: Job) -> JobOut:
    return JobOut.model_validate(job, from_attributes=True)


async def _load_job(db: AsyncSession, org_id: str, job_id: str) -> Job:
    job = (await db.execute(select(Job).where(Job.id == job_id, Job.org_id == org_id))).scalar_one_or_none()
    if job is None:
        raise not_found("job")
    return job


@router.post(
    "/orgs/{org_id}/jobs", status_code=202, response_model=JobOut, summary="Start a job",
    description="Queues a pipeline run and answers `202 Accepted` with a `Location` header. Follow progress with "
                "`GET .../jobs/{job_id}` or the SSE stream `.../events`. One run per organization at a time.",
    responses={404: {"description": "Upload not found"}, 409: {"description": "Queue full"},
               422: {"description": "Pipeline and upload do not match"}},
)
async def create_job(body: JobCreate, request: Request, response: Response, ctx: OrgContext = Depends(_WRITE),
                     db: AsyncSession = Depends(get_db)) -> JobOut:
    state = request.app.state
    enforce(request, "v1_run", state.settings.daas_rate_limit_run_per_min)
    kinds = PIPELINE_UPLOAD_KINDS[body.pipeline]
    if state.settings.cloud and not body.force_mock:
        # M-7: w chmurze zrodlem jest wylacznie plik klienta (albo dane syntetyczne); nigdy zasoby operatora z env
        if kinds is None:
            raise ProblemError(422, "mock_required", f"pipeline {body.pipeline} runs on synthetic data only: "
                               "set force_mock")
        if not body.upload_id:
            raise ProblemError(422, "upload_required", f"pipeline {body.pipeline} needs an upload_id "
                               "(or force_mock for synthetic data)")
    if body.upload_id:
        if kinds is None:
            raise ProblemError(422, "upload_not_applicable", f"pipeline {body.pipeline} does not read uploads")
        upload = (await db.execute(select(Upload).where(
            Upload.id == body.upload_id, Upload.org_id == ctx.org_id))).scalar_one_or_none()
        if upload is None:
            raise not_found("upload")
        if upload.kind not in kinds:
            raise ProblemError(422, "upload_kind_mismatch",
                               f"pipeline {body.pipeline} needs an upload of kind {' or '.join(kinds)}")
    pending = (await db.execute(select(func.count()).select_from(Job).where(
        Job.org_id == ctx.org_id, Job.status.in_(("queued", "running"))))).scalar_one()
    if pending >= MAX_PENDING_JOBS:
        raise ProblemError(409, "queue_full", f"at most {MAX_PENDING_JOBS} queued or running jobs per organization")
    job = Job(org_id=ctx.org_id, pipeline=body.pipeline, upload_id=body.upload_id, force_mock=body.force_mock,
              client_name=body.client_name, created_by=ctx.principal.actor_tag, message="queued")
    db.add(job)
    await db.flush()
    await record_event(db, request, org_id=ctx.org_id, actor_type=ctx.principal.actor_type,
                       actor_id=ctx.principal.actor_id, action="job.created", target=f"job:{job.id}",
                       meta={"job_id": job.id, "pipeline": body.pipeline, "upload_id": body.upload_id})
    await db.commit()
    state.executor.submit(job.id, ctx.org_id)
    response.headers["Location"] = f"/api/v1/orgs/{ctx.org_id}/jobs/{job.id}"
    return job_out(job)


@router.get("/orgs/{org_id}/jobs", response_model=JobPage, summary="List jobs (newest first)")
async def list_jobs(status: JobStatus | None = None, limit: int = Query(default=20, ge=1, le=100),
                    cursor: str | None = Query(default=None, max_length=26),
                    ctx: OrgContext = Depends(_READ), db: AsyncSession = Depends(get_db)) -> JobPage:
    query = select(Job).where(Job.org_id == ctx.org_id)
    if status:
        query = query.where(Job.status == status)
    if cursor:
        query = query.where(Job.id < cursor)
    rows = (await db.execute(query.order_by(Job.id.desc()).limit(limit + 1))).scalars().all()
    page = rows[:limit]
    return JobPage(items=[job_out(j) for j in page], next_cursor=page[-1].id if len(rows) > limit else None)


@router.get("/orgs/{org_id}/jobs/{job_id}", response_model=JobOut, summary="Get a job")
async def get_job(job_id: str, ctx: OrgContext = Depends(_READ), db: AsyncSession = Depends(get_db)) -> JobOut:
    return job_out(await _load_job(db, ctx.org_id, job_id))


@router.post("/orgs/{org_id}/jobs/{job_id}/cancel", response_model=JobOut, summary="Cancel a queued job",
             description="Only `queued` jobs can be cancelled; a running job finishes.",
             responses={409: {"description": "The job already started or finished"}})
async def cancel_job(job_id: str, request: Request, ctx: OrgContext = Depends(_WRITE),
                     db: AsyncSession = Depends(get_db)) -> JobOut:
    job = await _load_job(db, ctx.org_id, job_id)
    changed = await db.execute(update(Job).where(Job.id == job.id, Job.status == "queued").values(
        status="cancelled", finished_at=utcnow(), message="cancelled", progress=0))
    if changed.rowcount != 1:
        await db.rollback()
        raise ProblemError(409, "not_cancellable", "only queued jobs can be cancelled")
    await record_event(db, request, org_id=ctx.org_id, actor_type=ctx.principal.actor_type,
                       actor_id=ctx.principal.actor_id, action="job.cancelled", target=f"job:{job.id}",
                       meta={"job_id": job.id})
    await db.commit()
    await db.refresh(job)
    request.app.state.executor.bus.publish(job.id)
    return job_out(job)


# ----------------------------------------------------------------------------- SSE
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _snapshot(job: Job) -> dict:
    return json.loads(job_out(job).model_dump_json())


#: Fallback: bez zdarzenia z szyny stan zadania jest odczytywany co tyle sekund (zwykle budzi `JobBus.publish`).
SSE_FALLBACK_POLL_SEC = 5.0


async def _event_stream(request: Request, org_id: str, job_id: str, slot: StreamSlot) -> AsyncIterator[str]:
    state = request.app.state
    bus, heartbeat = state.executor.bus, state.settings.daas_sse_heartbeat_sec
    loop = asyncio.get_running_loop()
    deadline = loop.time() + state.settings.daas_sse_max_seconds
    event = bus.subscribe(job_id)
    last: tuple | None = None
    last_beat = loop.time()
    yield "retry: 3000\n\n"
    try:
        while True:
            event.clear()
            async with state.db_sessionmaker() as db:
                job = (await db.execute(select(Job).where(Job.id == job_id, Job.org_id == org_id))).scalar_one_or_none()
            if job is None:
                yield _sse("done", {"id": job_id, "status": "failed", "error_code": "job_missing"})
                return
            snap = _snapshot(job)
            state_now = (job.status, job.progress, job.message)
            if state_now != last:
                if last is None or job.status != last[0]:
                    yield _sse("status", snap)
                if last is not None and job.progress != last[1]:
                    yield _sse("progress", {"id": job.id, "progress": job.progress})
                if job.message and (last is None or job.message != last[2]):
                    yield _sse("log", {"id": job.id, "message": job.message, "ts": utcnow().isoformat()})
                last = state_now
                last_beat = loop.time()
            if job.status in TERMINAL:
                yield _sse("done", snap)
                return
            if bus.closing:
                yield ": server shutting down, reconnect\n\n"
                return
            if loop.time() >= deadline:
                yield ": maximum stream lifetime reached, reconnect\n\n"
                return
            if await request.is_disconnected():
                return
            now = loop.time()
            if now - last_beat >= heartbeat:
                yield ": heartbeat\n\n"
                last_beat = now
            timeout = max(0.05, min(SSE_FALLBACK_POLL_SEC, heartbeat, deadline - now))
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except TimeoutError:
                pass
    finally:
        bus.unsubscribe(job_id, event)
        slot.release()


@router.get(
    "/orgs/{org_id}/jobs/{job_id}/events", summary="Stream job events (SSE)",
    description="`text/event-stream`. Events: `status` (job snapshot on every status change), `progress` "
                "(`{id, progress}`), `log` (`{id, message, ts}`) and a final `done` (job snapshot); the stream closes "
                "after `done`. A comment line `: heartbeat` is sent every 15 seconds of silence. Limits: 5 open streams per "
                "caller and 100 in total (`429 sse_limit`); a stream lives at most 30 minutes (reconnect; the first event of a "
                "stream is always a full `status` snapshot).",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def job_events(job_id: str, request: Request, ctx: OrgContext = Depends(_READ),
                     db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    await _load_job(db, ctx.org_id, job_id)  # 404 zanim zaczniemy strumien
    await db.close()  # sesja zadania nie ma trzymac polaczenia przez caly strumien
    state = request.app.state
    bus = state.executor.bus
    if bus.closing:
        raise ProblemError(503, "shutting_down", "the server is shutting down, retry shortly",
                           headers={"Retry-After": "5"})
    slot = bus.open_stream(principal_key(request), per_key=state.settings.daas_sse_max_per_principal,
                           total=state.settings.daas_sse_max_total)
    if slot is None:
        raise ProblemError(429, "sse_limit", "too many open event streams; close one or retry shortly",
                           headers={"Retry-After": "5"})
    # slot zwalnia generator (finally) oraz zadanie w tle (gdy odpowiedz nie zdazyla wystartowac); zwolnienie jest idempotentne
    return StreamingResponse(
        _event_stream(request, ctx.org_id, job_id, slot), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}, background=BackgroundTask(slot.release))
