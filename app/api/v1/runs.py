"""Runy i raporty: lista, szczegoly, plik raportu (html|md|json), weryfikacja skrotu, status artefaktow."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError, not_found
from app.api.routes import REPORT_HEADERS
from app.api.v1.deps import OrgContext, org_access
from app.api.v1.schemas import (
    EvidenceCheck,
    EvidenceOut,
    RunDetail,
    RunOut,
    RunPage,
    VerifyIn,
    VerifyOut,
)
from app.control import scopes as sc
from app.control.db import get_db
from app.control.models import RunRecord
from app.control.report_paths import resolve_report
from app.core.models import RunResult
from app.core.redact import scrub_paths

log = logging.getLogger(__name__)
router = APIRouter(tags=["runs"])

_READ = org_access(sc.RUNS_READ)
_REPORTS = org_access(sc.REPORTS_READ)

FORMATS = ("html", "md", "json")
MEDIA_TYPES = {"html": "text/html; charset=utf-8", "md": "text/markdown; charset=utf-8", "json": "application/json"}
#: Pole `RunResult`/wiersza DuckDB z sciezka pliku raportu danego formatu.
PATH_KEYS = {"html": "report_html_path", "md": "report_path", "json": "report_json_path"}


def _links(org_id: str, record: RunRecord) -> dict[str, str]:
    base = f"/api/v1/orgs/{org_id}/runs/{record.run_id}/report"
    return {fmt: f"{base}?fmt={fmt}" for fmt, digest in record.report_sha256.items() if digest}


def run_out(record: RunRecord) -> RunOut:
    return RunOut(run_id=record.run_id, pipeline=record.pipeline, status=record.status, created_at=record.created_at,
                  job_id=record.job_id, report_sha256=record.report_sha256, links=_links(record.org_id, record),
                  period_from=record.period_from, period_to=record.period_to)


async def _load_record(db: AsyncSession, org_id: str, run_id: str) -> RunRecord:
    record = (await db.execute(select(RunRecord).where(
        RunRecord.run_id == run_id, RunRecord.org_id == org_id))).scalar_one_or_none()
    if record is None:
        raise not_found("run")
    return record


@router.get("/orgs/{org_id}/runs", response_model=RunPage, summary="List runs (newest first)")
async def list_runs(pipeline: str | None = Query(default=None, max_length=40),
                    limit: int = Query(default=20, ge=1, le=100),
                    cursor: str | None = Query(default=None, max_length=80,
                                               description="`next_cursor` z poprzedniej strony"),
                    ctx: OrgContext = Depends(_READ), db: AsyncSession = Depends(get_db)) -> RunPage:
    query = select(RunRecord).where(RunRecord.org_id == ctx.org_id)
    if pipeline:
        query = query.where(RunRecord.pipeline == pipeline)
    if cursor:
        try:
            stamp, _, last_id = cursor.partition("|")
            at = datetime.fromisoformat(stamp)
        except ValueError:
            raise ProblemError(422, "invalid_cursor", "cursor is not valid") from None
        query = query.where(or_(RunRecord.created_at < at, (RunRecord.created_at == at) & (RunRecord.run_id < last_id)))
    rows = (await db.execute(query.order_by(RunRecord.created_at.desc(), RunRecord.run_id.desc())
                             .limit(limit + 1))).scalars().all()
    page = rows[:limit]
    nxt = f"{page[-1].created_at.isoformat()}|{page[-1].run_id}" if len(rows) > limit else None
    return RunPage(items=[run_out(r) for r in page], next_cursor=nxt)


def _tenant_run(state, org_id: str, run_id: str) -> dict | None:
    return state.tenants.get(org_id).db.get_run(run_id)


@router.get("/orgs/{org_id}/runs/{run_id}", response_model=RunDetail, summary="Get a run",
            description="Run record with report hashes, links and summary metrics. Report paths and raw error text "
                        "are never returned.")
async def get_run(run_id: str, request: Request, ctx: OrgContext = Depends(_READ),
                  db: AsyncSession = Depends(get_db)) -> RunDetail:
    record = await _load_record(db, ctx.org_id, run_id)
    row = await asyncio.to_thread(_tenant_run, request.app.state, ctx.org_id, run_id)
    extra: dict = {}
    if row:
        result = RunResult.model_validate(row)
        extra = {
            "data_status": result.data_status.value, "records_processed": result.records_processed,
            "narrative_source": result.narrative_source, "execution_time_sec": result.execution_time_sec,
            "source_detail": scrub_paths(result.source_detail),
            "error": f"{(result.error or '').split(':', 1)[0]}" if result.error else None, "metrics": result.metrics,
        }
    return RunDetail(**run_out(record).model_dump(), **extra)


def _read_report(path: Path) -> tuple[bytes, str]:
    """Jeden odczyt pliku: zwraca (bajty, sha256 tych samych bajtow) — hash i odpowiedz nie moga sie rozjechac."""
    body = path.read_bytes()
    return body, hashlib.sha256(body).hexdigest()


def _purged(record: RunRecord) -> bool:
    """True, gdy retencja usunela pliki raportow (rekord i hashe zostaja). Odczyt tolerancyjny dla starszych schematow."""
    return getattr(record, "report_purged_at", None) is not None


_resolve_report = resolve_report   # wspolny resolver (retencja uzywa tego samego)


def _etag_matches(header: str, digest: str) -> bool:
    """`If-None-Match`: lista wartosci, slabe `W/"..."`, `*`; porownanie slabe (RFC 9110 13.1.2)."""
    for part in header.split(","):
        tag = part.strip()
        if tag == "*":
            return True
        if tag[:2] in ("W/", "w/"):
            tag = tag[2:]
        if tag.strip('" ') == digest:
            return True
    return False


@router.get(
    "/orgs/{org_id}/runs/{run_id}/report", summary="Download a report",
    description="`fmt=html|md|json`. Sent with a restrictive `Content-Security-Policy`, `nosniff`, `ETag` = SHA-256 "
                "of the file and `Cache-Control: private, no-store`. Embed the HTML only in a sandboxed iframe. "
                "`410 report_expired` once the retention job removed the files (the run record and hashes stay).",
    response_class=Response,
    responses={200: {"content": {"text/html": {}, "text/markdown": {}, "application/json": {}}},
               304: {"description": "ETag matches"},
               410: {"description": "report_expired: files removed by retention"}},
)
async def get_report(run_id: str, request: Request, fmt: Literal["html", "md", "json"] = Query(default="html"),
                     ctx: OrgContext = Depends(_REPORTS), db: AsyncSession = Depends(get_db)) -> Response:
    record = await _load_record(db, ctx.org_id, run_id)
    if _purged(record):
        raise ProblemError(410, "report_expired", "the report files were removed by the retention policy")
    expected = record.report_sha256.get(fmt)
    if not expected:
        raise not_found("report")
    row = await asyncio.to_thread(_tenant_run, request.app.state, ctx.org_id, run_id)
    paths = request.app.state.tenants.paths(ctx.org_id)
    # sciezka pochodzi z bazy najemcy (nie od klienta); plik musi lezec w katalogu raportow tej organizacji
    path = _resolve_report(paths.reports, (row or {}).get(PATH_KEYS[fmt]))
    if path is None:
        raise not_found("report file")
    body, actual = await asyncio.to_thread(_read_report, path)
    if actual != expected:
        log.error("report integrity mismatch run_id=%s fmt=%s", run_id, fmt)
        raise ProblemError(500, "report_integrity_error", "the stored report does not match its recorded hash")
    headers = {**REPORT_HEADERS, "ETag": f'"{expected}"', "Cache-Control": "private, no-store"}
    if _etag_matches(request.headers.get("if-none-match", ""), expected):
        return Response(status_code=304, headers=headers)
    safe_name = f"{record.pipeline}-{record.run_id}.{fmt}"
    if fmt == "html":
        return HTMLResponse(body, headers={**headers, "Content-Disposition": f'inline; filename="{safe_name}"'})
    return Response(body, media_type=MEDIA_TYPES[fmt],
                    headers={**headers, "Content-Disposition": f'attachment; filename="{safe_name}"'})


@router.post(
    "/orgs/{org_id}/verify", response_model=VerifyOut, summary="Check a report hash",
    description="Tells whether a SHA-256 belongs to a report of this organization. The answer never contains "
                "report content.",
)
async def verify(body: VerifyIn, ctx: OrgContext = Depends(_REPORTS), db: AsyncSession = Depends(get_db)) -> VerifyOut:
    hits = (await db.execute(select(RunRecord).where(RunRecord.org_id == ctx.org_id, or_(
        RunRecord.sha256_html == body.sha256, RunRecord.sha256_md == body.sha256,
        RunRecord.sha256_json == body.sha256,
    )).order_by(RunRecord.created_at.desc()).limit(1))).scalars().all()
    if not hits:
        return VerifyOut(known=False)
    record = hits[0]
    fmt = next(f for f, d in record.report_sha256.items() if d == body.sha256)
    return VerifyOut(known=True, run_id=record.run_id, pipeline=record.pipeline, created_at=record.created_at,
                     format=fmt)


def _evidence_rows(state, org_id: str, run_id: str) -> tuple[dict | None, list[dict]]:
    db = state.tenants.get(org_id).db
    with db.connect() as con:
        host = con.execute(
            "SELECT host_name, collected_at_utc, package_sha256, integrity FROM uksc_hosts WHERE run_id = ?",
            [run_id]).fetchone()
        checks = con.execute(
            "SELECT control_id, uksc_ref, title, status, evidence_source, evidence_hash, evidence_collected_at "
            "FROM uksc_checks WHERE run_id = ? ORDER BY control_id", [run_id]).fetchall()
    head = None if host is None else {"host": host[0], "collected_at": host[1], "package_sha256": host[2],
                                      "integrity": host[3]}
    return head, [
        {"control_id": c[0], "requirement": c[1], "title": c[2], "status": c[3],
         "artifact": {"source": c[4], "sha256": c[5], "collected_at": c[6]}} for c in checks]


def _aware(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


@router.get(
    "/orgs/{org_id}/evidence", response_model=EvidenceOut, summary="Artifact status of the latest collector run",
    description="Control -> requirement -> status -> artifact (source and SHA-256) from the latest successful "
                "`uksc_evidence` run. Artifact contents are not returned.",
    responses={404: {"description": "No collector run yet"}},
)
async def evidence(request: Request, ctx: OrgContext = Depends(_REPORTS),
                   db: AsyncSession = Depends(get_db)) -> EvidenceOut:
    record = (await db.execute(select(RunRecord).where(
        RunRecord.org_id == ctx.org_id, RunRecord.pipeline == "uksc_evidence", RunRecord.status != "FAILED",
    ).order_by(RunRecord.created_at.desc(), RunRecord.run_id.desc()).limit(1))).scalar_one_or_none()
    if record is None:
        raise not_found("collector run")
    head, rows = await asyncio.to_thread(_evidence_rows, request.app.state, ctx.org_id, record.run_id)
    head = head or {}
    return EvidenceOut(
        run_id=record.run_id, host=head.get("host"), collected_at=_aware(head.get("collected_at")),
        package_sha256=head.get("package_sha256"), integrity=head.get("integrity"),
        checks=[EvidenceCheck(**{**r, "artifact": {**r["artifact"],
                                                   "collected_at": _aware(r["artifact"]["collected_at"])}})
                for r in rows])
