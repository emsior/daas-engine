"""Uploady plikow organizacji: CSV, XLSX (import z limitami) i paczka JSON kolektora artefaktow."""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.problems import ProblemError
from app.api.routes import _enforce_policy_on_path
from app.api.v1.deps import OrgContext, org_access
from app.api.v1.jobs import queue_job
from app.api.v1.plan_guard import runs_limit_reached
from app.api.v1.schemas import JobCreate, UploadOut
from app.control import scopes as sc
from app.control import service
from app.control.audit import record_event
from app.control.db import get_db
from app.control.ids import new_upload_id
from app.control.models import OrgSchedule, Upload
from app.control.types import utcnow
from app.core import xlsx_import
from app.core.models import UkscPackage
from app.core.ratelimit_v1 import enforce
from app.core.upload_guard import (
    CHUNK_SIZE,
    MAX_RECORDS,
    MAX_UPLOAD_BYTES,
    ReasonCode,
    UploadRejected,
    cleanup_stale_tmp,
    content_hash,
    dir_usage_bytes,
    ensure_inside_root,
    is_xlsx_name,
    remove_quietly,
    validate_csv_bytes,
    validate_original_filename,
)
from app.sources.uksc_source import MAX_PACKAGE_BYTES
from app.sources.xlsx_formulas import formula_warning

log = logging.getLogger(__name__)
router = APIRouter(tags=["uploads"])

_WRITE = org_access(sc.UPLOADS_WRITE)

#: Rozszerzenie pliku klienta -> rodzaj uploadu i pipeline proponowany do uruchomienia.
KINDS = {".csv": "csv", ".xlsx": "xlsx", ".json": "package"}
SUGGESTED_PIPELINE = {"csv": "ecommerce_demo", "xlsx": "ecommerce_demo", "package": "uksc_evidence"}


def _kind_of(filename: str | None) -> str:
    """Waliduje nazwe (te same reguly co legacy; dodatkowo `.json` dla paczki) i zwraca rodzaj."""
    name = (filename or "").strip()
    if name.lower().endswith(".json"):
        validate_original_filename(name[:-5] + ".csv")  # dlugosc i znaki niedozwolone: ta sama kontrola co dla CSV
        return "package"
    validate_original_filename(name)
    return KINDS[".xlsx"] if is_xlsx_name(name) else KINDS[".csv"]


async def _stream_to_temp(file: UploadFile, tmp_path: Path, *, limit: int, quota_remaining: int | None) -> int:
    """Zapis strumieniowy z limitem egzekwowanym W TRAKCIE odbioru (nie po wczytaniu calosci)."""
    total = 0
    with tmp_path.open("wb") as fh:
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise UploadRejected(ReasonCode.FILE_TOO_LARGE, 413, f"plik przekracza {limit} bajtow")
            if quota_remaining is not None and total > quota_remaining:
                raise UploadRejected(ReasonCode.QUOTA_EXCEEDED, 413, "kwota uploadow wyczerpana")
            fh.write(chunk)
    return total


def _validate_package(raw: bytes) -> None:
    """Paczka kolektora: poprawny JSON UTF-8 zgodny ze schematem `UkscPackage`. Bez ech tresci w bledzie."""
    try:
        UkscPackage.model_validate(json.loads(raw.decode("utf-8-sig")))
    except Exception as exc:  # noqa: BLE001
        log.info("package rejected: %s", type(exc).__name__)
        raise UploadRejected(ReasonCode.SCHEMA_MISMATCH, 422, "paczka nie spelnia schematu kolektora") from None


async def _auto_run(request: Request, db: AsyncSession, ctx: OrgContext, upload: Upload) -> str | None:
    """Harmonogram `auto_run_on_upload`: zadanie dla `suggested_pipeline` ta sama droga co `POST /jobs`
    (`queue_job`). Limit planu, pelna kolejka ani zaden inny blad NIE psuja uploadu (ten jest juz zapisany):
    wynik to None i audyt `schedule.auto_run_skipped` z kodem w `reason`."""
    schedule = await db.get(OrgSchedule, ctx.org_id)
    pipeline = SUGGESTED_PIPELINE.get(upload.kind)
    if schedule is None or not schedule.auto_run_on_upload or pipeline is None:
        return None
    state = request.app.state
    actor = ctx.principal
    org_id, upload_id = ctx.org_id, upload.id  # po rollbacku obiekty ORM sa wygaszone: identyfikatory z gory
    actor_type, actor_id = actor.actor_type, actor.actor_id
    reason: str
    try:
        reached = await runs_limit_reached(db, ctx.org)
        if reached is not None:
            reason = "plan_limit"
        else:
            job = await queue_job(db, state, org_id, actor, JobCreate(pipeline=pipeline, upload_id=upload_id),
                                  request=request, audit_reason="auto_run")
            return job.id
    except ProblemError as problem:
        reason = problem.code
    except Exception:  # noqa: BLE001
        log.exception("auto-run after upload failed upload_id=%s", upload_id)
        reason = "internal_error"
    try:
        await db.rollback()
        await record_event(db, request, org_id=org_id, actor_type=actor_type, actor_id=actor_id,
                           action="schedule.auto_run_skipped", target=f"upload:{upload_id}",
                           meta={"upload_id": upload_id, "pipeline": pipeline, "reason": reason})
        await db.commit()
    except Exception:  # noqa: BLE001
        log.exception("auto-run skip audit failed upload_id=%s", upload_id)
    return None


@router.post(
    "/orgs/{org_id}/uploads", status_code=201, response_model=UploadOut, summary="Upload a file",
    description="Accepts a CSV or XLSX export (up to 10 MiB, XLSX with container, macro, sheet and row limits) or a "
                "collector package (`.json`, up to 5 MiB, validated against the collector schema). Returns the file "
                "id and, for CSV/XLSX, the detected column mapping. A workbook with several sheets needs `sheet`.",
    responses={413: {"description": "File or quota too large"}, 415: {"description": "Unsupported file type"},
               422: {"description": "File failed validation"}},
)
async def create_upload(request: Request, file: UploadFile = File(...),
                        sheet: str | None = Form(default=None, max_length=xlsx_import.MAX_SHEET_NAME_LEN),
                        ctx: OrgContext = Depends(_WRITE), db: AsyncSession = Depends(get_db)) -> UploadOut:
    return await ingest_upload(request, db, ctx, file, sheet)


async def ingest_upload(request: Request, db: AsyncSession, ctx: OrgContext, file: UploadFile,
                        sheet: str | None = None) -> UploadOut:
    """Walidacja i zapis uploadu (logika wspolna v1 i /mcp): limit `v1_upload`, nazwa pliku, `upload_guard`,
    import XLSX, schemat paczki, policy, mapper kolumn, kwota katalogu, licznik zuzycia, audyt `upload.created`.
    Odrzucenie -> `ProblemError` z kodem powodu (`extra.reason_code`) i audytem `auth_denied`. Limit planu
    sprawdza wolajacy (`plan_guard` / `check_plan_limit`)."""
    from app.sources.csv_mapper import map_dataframe, read_client_csv

    state = request.app.state
    settings = state.settings
    enforce(request, "v1_upload", settings.daas_rate_limit_upload_per_min)
    paths = await run_in_threadpool(lambda: state.tenants.paths(ctx.org_id).ensure())
    root = paths.uploads
    actor = ctx.principal

    async def denied(reason: str, status: int, message: str, size: int = 0) -> ProblemError:
        await record_event(db, request, org_id=ctx.org_id, actor_type=actor.actor_type, actor_id=actor.actor_id,
                           action="auth_denied", target="upload", meta={"reason": reason, "bytes": size})
        await db.commit()
        return ProblemError(status, reason, message, extra={"reason_code": reason})

    cleanup_stale_tmp(root)
    quota = settings.daas_upload_quota_bytes
    quota_remaining = max(0, quota - dir_usage_bytes(root)) if quota > 0 else None

    upload_id = new_upload_id()
    tmp_path = ensure_inside_root(root / f"{upload_id}.tmp", root)
    size = 0
    final_path: Path | None = None
    formula_note: str | None = None
    try:
        kind = _kind_of(file.filename)
        limit = MAX_PACKAGE_BYTES if kind == "package" else MAX_UPLOAD_BYTES
        size = await _stream_to_temp(file, tmp_path, limit=limit, quota_remaining=quota_remaining)
        raw = tmp_path.read_bytes()
        received_sha = content_hash(raw)  # skrot pliku w postaci, w jakiej przyszedl
        suffix = ".json" if kind == "package" else ".csv"
        final_path = ensure_inside_root(root / f"{upload_id}{suffix}", root)

        if kind == "package":
            if not raw.strip():
                raise UploadRejected(ReasonCode.FILE_EMPTY, 422, "pusty plik")
            _validate_package(raw)
        else:
            max_records = MAX_RECORDS
            if kind == "xlsx":
                raw, _ = await run_in_threadpool(xlsx_import.xlsx_to_csv_bytes, tmp_path, sheet)
                # C14: skan formul na zwalidowanym XLSX, PRZED nadpisaniem pliku tymczasowego konwersja CSV
                formula_note = await run_in_threadpool(formula_warning, tmp_path, sheet)
                tmp_path.write_bytes(raw)
                max_records = xlsx_import.MAX_XLSX_ROWS
            validate_csv_bytes(raw, max_records=max_records)
        _enforce_policy_on_path(settings, final_path, root, paths.audit_log)
        os.replace(tmp_path, final_path)

        mapping: dict = {}
        if kind != "package":
            df = await run_in_threadpool(read_client_csv, str(final_path))
            _, rep = await run_in_threadpool(map_dataframe, df)
            mapping = {
                "rows": rep.rows_out, "columns": [str(c) for c in df.columns], "mapping": rep.mapping,
                "unmapped_columns": rep.unmapped_columns, "generated": rep.generated, "warnings": rep.warnings,
                "confidence": rep.confidence, "ready": rep.rows_out > 0,
            }
            if formula_note:
                mapping["warnings"] = [*rep.warnings, formula_note]

        row = Upload(
            id=upload_id, org_id=ctx.org_id, kind=kind, filename=(file.filename or "")[:128], bytes=size,
            sha256=received_sha, path=final_path.name,
            retention_until=utcnow() + timedelta(days=ctx.org.retention_days))
        db.add(row)
        await service.add_usage(db, ctx.org_id, upload_bytes=size)
        await record_event(db, request, org_id=ctx.org_id, actor_type=actor.actor_type, actor_id=actor.actor_id,
                           action="upload.created", target=f"upload:{upload_id}",
                           meta={"upload_id": upload_id, "kind": kind, "bytes": size, "sha256": received_sha})
        await db.commit()
        await db.refresh(row)
        out = UploadOut(
            id=row.id, kind=kind, filename=row.filename, bytes=row.bytes, sha256=row.sha256,
            created_at=row.created_at, retention_until=row.retention_until,
            suggested_pipeline=SUGGESTED_PIPELINE[kind], **mapping)
        out.auto_job_id = await _auto_run(request, db, ctx, row)
        return out

    except UploadRejected as rejected:
        if final_path is not None:
            remove_quietly(final_path)
        raise await denied(rejected.reason, rejected.http_status, rejected.message, size) from None
    except ProblemError:
        if final_path is not None:
            remove_quietly(final_path)
        raise
    except Exception as exc:  # noqa: BLE001
        # parser moze podniesc wyjatek niosacy tresc pliku: do klienta idzie tylko komunikat ogolny
        log.exception("v1 upload processing failed upload_id=%s", upload_id)
        if final_path is not None:
            remove_quietly(final_path)
        raise await denied(ReasonCode.PARSER_ERROR, 422, "nie udalo sie przetworzyc pliku", size) from exc
    finally:
        remove_quietly(tmp_path)
