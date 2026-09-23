"""Endpointy FastAPI: /health, /pipelines, /run, /runs/latest (+ /runs, /runs/{id})."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from app.core.models import HealthResponse, PipelineInfo, RunRequest, RunResult, UploadResponse
from app.core.upload_guard import (
    CHUNK_SIZE,
    MAX_UPLOAD_BYTES,
    ReasonCode,
    UploadRejected,
    append_audit,
    build_audit_event,
    content_hash,
    ensure_inside_root,
    final_path_for,
    new_upload_id,
    safe_deployment_id,
    temp_path_for,
    validate_csv_bytes,
    validate_original_filename,
)
from app.pipelines.runner import PIPELINES, PipelineRunner

router = APIRouter()


def _runner(request: Request) -> PipelineRunner:
    return request.app.state.runner


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> HealthResponse:
    s = _runner(request).settings
    return HealthResponse(
        app=s.app_name,
        version=s.app_version,
        env=s.app_env,
        duckdb_path=str(s.duckdb_file),
        secrets=s.secrets_status(),
        timestamp=datetime.utcnow(),
    )


@router.get("/pipelines", response_model=list[PipelineInfo], tags=["pipelines"])
def pipelines(request: Request) -> list[PipelineInfo]:
    return _runner(request).list_pipelines()


@router.post("/run", response_model=RunResult, tags=["pipelines"])
def run(body: RunRequest, request: Request) -> RunResult:
    if body.pipeline not in PIPELINES:
        raise HTTPException(status_code=404, detail=f"unknown pipeline: {body.pipeline}")
    return _runner(request).run(body)


@router.get("/runs/latest", response_model=RunResult, tags=["runs"])
def runs_latest(request: Request, pipeline: str | None = Query(default=None)) -> RunResult:
    row = _runner(request).db.latest_run(pipeline)
    if not row:
        raise HTTPException(status_code=404, detail="no runs yet")
    return RunResult.model_validate(row)


@router.get("/runs", response_model=list[RunResult], tags=["runs"])
def runs_list(request: Request, limit: int = Query(default=20, ge=1, le=200)) -> list[RunResult]:
    return [RunResult.model_validate(r) for r in _runner(request).db.list_runs(limit)]


@router.get("/runs/{run_id}", response_model=RunResult, tags=["runs"])
def runs_get(run_id: str, request: Request) -> RunResult:
    row = _runner(request).db.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    return RunResult.model_validate(row)


@router.get("/stats", tags=["system"])
def stats(request: Request) -> dict:
    return _runner(request).db.table_counts()


# ---------------------------------------------------------------------------
# Plik klienta: upload -> podgląd mapowania -> gotowy payload do /run
# ---------------------------------------------------------------------------
def _uploads_root(settings) -> Path:
    """Runtime root tego wdrozenia. Model: jeden klient = jedna instancja."""
    root = settings.reports_path.parent / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


async def _stream_to_temp(upload_file: UploadFile, tmp_path: Path) -> int:
    """Zapisuje strumien do pliku tymczasowego, pilnujac limitu w trakcie.

    Limit egzekwowany jest PODCZAS odbierania, a nie po wczytaniu calosci —
    inaczej kontrola rozmiaru nie chronilaby przed wyczerpaniem pamieci,
    tylko informowala o nim po fakcie.

    Uchwyt zostaje zamkniety przed powrotem z funkcji, zeby os.replace()
    na Windows mial do czynienia z plikiem bez otwartych deskryptorow.
    """
    total = 0
    with tmp_path.open("wb") as fh:
        while True:
            chunk = await upload_file.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise UploadRejected(
                    ReasonCode.FILE_TOO_LARGE, 413,
                    f"plik przekracza {MAX_UPLOAD_BYTES} bajtow",
                )
            fh.write(chunk)
    return total


def _enforce_policy_on_path(settings, final_path: Path, root: Path, audit_log: Path) -> None:
    """Wywoluje warstwe policy na rzeczywistej sciezce docelowej.

    Przy DAAS_POLICY_ENFORCE=false nie robi nic — zachowana kompatybilnosc MVP.
    Przy true dziala fail-closed: PolicyError konczy sie odmowa uploadu.
    """
    if not getattr(settings, "daas_policy_enforce", False):
        return

    from app.core.policy import Budget, PolicyError, ReadFile, enforce

    try:
        enforce(
            tool="read_file",
            raw_args={"path": str(final_path)},
            schema=ReadFile,
            fn=lambda path: path,
            budget=Budget(),
            role="analyst",
            audit_log=audit_log,
            workspace=root,
        )
    except PolicyError as exc:
        raise UploadRejected(ReasonCode.POLICY_DENIED, 403, "odmowa polityki dostepu") from exc


@router.post("/upload", response_model=UploadResponse, tags=["pipelines"])
async def upload(request: Request, file: UploadFile = File(...),
                 client_name: str | None = Form(default=None)) -> UploadResponse:
    """Przyjmuje plik CSV klienta i zwraca wykryte mapowanie kolumn.

    Kolejnosc: walidacja nazwy -> strumieniowy zapis tymczasowy -> walidacja
    tresci -> policy -> atomowa finalizacja. Plik finalny powstaje dopiero po
    przejsciu wszystkich kontroli; kazde niepowodzenie sprzata plik tymczasowy.

    Nazwa przyslana przez klienta NIE bierze udzialu w budowie sciezki —
    docelowa nazwe (<uuid>.csv) generuje serwer.
    """
    from app.sources.csv_mapper import map_dataframe, read_client_csv

    settings = _runner(request).settings
    root = _uploads_root(settings)
    audit_log = settings.audit_log_path
    deployment_id = safe_deployment_id(root)

    upload_id = new_upload_id()
    tmp_path = ensure_inside_root(temp_path_for(upload_id, root), root)
    final_path = ensure_inside_root(final_path_for(upload_id, root), root)

    size_bytes = 0
    finalized = False

    try:
        validate_original_filename(file.filename)
        size_bytes = await _stream_to_temp(file, tmp_path)

        raw = tmp_path.read_bytes()
        result = validate_csv_bytes(raw)

        _enforce_policy_on_path(settings, final_path, root, audit_log)

        os.replace(tmp_path, final_path)
        finalized = True

        df = read_client_csv(str(final_path))
        _, rep = map_dataframe(df)

        append_audit(build_audit_event(
            allowed=True, reason=ReasonCode.OK, deployment_id=deployment_id,
            size_bytes=size_bytes, upload_id=upload_id,
            encoding=result.encoding, content_sha256=content_hash(raw),
        ), audit_log)

        return UploadResponse(
            upload_id=upload_id,
            source_path=str(final_path),
            filename=final_path.name,
            rows=rep.rows_out,
            columns=[str(c) for c in df.columns],
            mapping=rep.mapping,
            unmapped_columns=rep.unmapped_columns,
            generated=rep.generated,
            warnings=rep.warnings,
            confidence=rep.confidence,
            ready=rep.rows_out > 0,
            run_payload={"pipeline": "ecommerce_demo", "source_path": str(final_path),
                         "client_name": client_name},
        )

    except UploadRejected as rejected:
        append_audit(build_audit_event(
            allowed=False, reason=rejected.reason, deployment_id=deployment_id,
            size_bytes=size_bytes,
        ), audit_log)
        raise HTTPException(status_code=rejected.http_status, detail=rejected.message) from None

    except Exception as exc:  # noqa: BLE001
        # Parser moze podniesc wyjatek niosacy tresc pliku klienta w komunikacie —
        # dlatego do odpowiedzi trafia wylacznie komunikat ogolny.
        if finalized:
            final_path.unlink(missing_ok=True)
        append_audit(build_audit_event(
            allowed=False, reason=ReasonCode.PARSER_ERROR, deployment_id=deployment_id,
            size_bytes=size_bytes,
        ), audit_log)
        raise HTTPException(status_code=422, detail="nie udalo sie przetworzyc pliku") from exc

    finally:
        # Plik tymczasowy nie moze przetrwac zadnej sciezki wyjscia.
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Raporty: link do otwarcia w przeglądarce / pobrania
# ---------------------------------------------------------------------------
@router.get("/reports/{pipeline}/latest", tags=["runs"])
def report_latest(pipeline: str, request: Request, fmt: str = Query(default="html", pattern="^(html|md|json)$")):
    if pipeline not in PIPELINES:
        raise HTTPException(status_code=404, detail=f"unknown pipeline: {pipeline}")
    path = _runner(request).settings.reports_path / f"{pipeline}_latest.{fmt}"
    if not path.exists():
        raise HTTPException(status_code=404, detail="no report yet — run the pipeline first")
    return _serve_report(path, fmt)


@router.get("/reports/run/{run_id}", tags=["runs"])
def report_by_run(run_id: str, request: Request, fmt: str = Query(default="html", pattern="^(html|md|json)$")):
    row = _runner(request).db.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    key = {"html": "report_html_path", "md": "report_path", "json": "report_json_path"}[fmt]
    path = Path(row.get(key) or "")
    if not row.get(key) or not path.exists():
        raise HTTPException(status_code=404, detail="report file missing for this run")
    return _serve_report(path, fmt)


def _serve_report(path: Path, fmt: str):
    if fmt == "html":
        return HTMLResponse(path.read_text(encoding="utf-8"))
    media = "application/json" if fmt == "json" else "text/markdown; charset=utf-8"
    return FileResponse(path, media_type=media, filename=path.name)
