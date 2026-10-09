"""Endpointy FastAPI.

Dwa routery:
    * `public_router` — `/health` i `/ready` (zredagowane, bez klucza),
    * `router`        — wszystko inne, z zaleznoscia `require_api_key` na calym routerze.
Nowa trasa dodana do `router` jest chroniona automatycznie; test `tests/test_auth.py` pilnuje reszty.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.core import audit, xlsx_import
from app.core.auth import require_api_key
from app.core.config import installed_version
from app.core.models import (
    HealthResponse,
    PipelineInfo,
    ReadyResponse,
    RunRequest,
    RunResult,
    UploadResponse,
)
from app.core.ratelimit import limit_ip, limit_run, limit_upload
from app.core.redact import redact_run
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
    final_path_for,
    is_xlsx_name,
    new_upload_id,
    temp_path_for,
    validate_csv_bytes,
    validate_original_filename,
)
from app.pipelines.runner import PIPELINES, PipelineRunner

log = logging.getLogger(__name__)

public_router = APIRouter()
# kolejnosc ma znaczenie: limit per IP (takze dla anonimowych), potem klucz API
router = APIRouter(dependencies=[Depends(limit_ip), Depends(require_api_key)])


def _runner(request: Request) -> PipelineRunner:
    return request.app.state.runner


@public_router.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> HealthResponse:
    """Liveness. Publiczny i minimalny: status, wersja, tryb. Bez sciezek, konfiguracji i sekretow."""
    s = request.app.state.settings
    return HealthResponse(app=s.app_name, version=installed_version(s.app_version), timestamp=datetime.utcnow())


@public_router.get("/ready", response_model=ReadyResponse, tags=["system"], responses={503: {"model": ReadyResponse}})
def ready(request: Request):
    """Readiness: runner istnieje, a baza odpowiada. Przy bledzie 503 bez szczegolow (szczegoly w logu)."""
    try:
        _runner(request).db.table_counts()
    except Exception:  # noqa: BLE001
        log.exception("readiness check failed")
        return JSONResponse(ReadyResponse(status="not_ready").model_dump(), status_code=503)
    return ReadyResponse(status="ready")


@router.get("/pipelines", response_model=list[PipelineInfo], tags=["pipelines"])
def pipelines(request: Request) -> list[PipelineInfo]:
    return _runner(request).list_pipelines()


@router.post("/run", response_model=RunResult, tags=["pipelines"], dependencies=[Depends(limit_run)])
def run(body: RunRequest, request: Request) -> RunResult:
    if body.pipeline not in PIPELINES:
        raise HTTPException(status_code=404, detail=f"unknown pipeline: {body.pipeline}")
    runner = _runner(request)
    if body.upload_id:
        # plik z POST /upload: sciezke sklada serwer z identyfikatora (wzorzec ^[0-9a-f]{32}$), nie klient
        path = _uploads_root(runner.settings) / f"{body.upload_id}.csv"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="upload not found")
        body = body.model_copy(update={"source_path": str(path), "upload_id": None})
    result = runner.run(body)
    audit.record(
        request, action="run", allowed=True,
        reason=ReasonCode.RUN_FAILED if result.run_status.value == "FAILED" else ReasonCode.OK,
        pipeline=result.pipeline, run_id=result.run_id,
    )
    return redact_run(result)


@router.get("/runs/latest", response_model=RunResult, tags=["runs"])
def runs_latest(request: Request, pipeline: str | None = Query(default=None)) -> RunResult:
    row = _runner(request).db.latest_run(pipeline)
    if not row:
        raise HTTPException(status_code=404, detail="no runs yet")
    return redact_run(RunResult.model_validate(row))


@router.get("/runs", response_model=list[RunResult], tags=["runs"])
def runs_list(request: Request, limit: int = Query(default=20, ge=1, le=200)) -> list[RunResult]:
    return [redact_run(RunResult.model_validate(r)) for r in _runner(request).db.list_runs(limit)]


@router.get("/runs/{run_id}", response_model=RunResult, tags=["runs"])
def runs_get(run_id: str, request: Request) -> RunResult:
    row = _runner(request).db.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    return redact_run(RunResult.model_validate(row))


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


async def _stream_to_temp(upload_file: UploadFile, tmp_path: Path, quota_remaining: int | None = None) -> int:
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
            if quota_remaining is not None and total > quota_remaining:
                raise UploadRejected(ReasonCode.QUOTA_EXCEEDED, 413, "kwota uploadow wyczerpana")
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


@router.post("/upload", response_model=UploadResponse, tags=["pipelines"], dependencies=[Depends(limit_upload)])
async def upload(request: Request, file: UploadFile = File(...),
                 client_name: str | None = Form(default=None),
                 sheet: str | None = Form(default=None, max_length=xlsx_import.MAX_SHEET_NAME_LEN)) -> UploadResponse:
    """Przyjmuje plik CSV lub XLSX klienta i zwraca wykryte mapowanie kolumn.

    XLSX (limity i kontrole: `app.core.xlsx_import`) jest konwertowany do CSV i dalej idzie ta sama
    sciezka co CSV. Skoroszyt z wieloma arkuszami wymaga pola `sheet`; pole jest ignorowane dla CSV.

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

    # Porzucone *.tmp (przerwane uploady) nie licza sie do kwoty i sa sprzatane przy kazdym uploadzie.
    cleanup_stale_tmp(root)
    quota = settings.daas_upload_quota_bytes
    quota_remaining = max(0, quota - dir_usage_bytes(root)) if quota > 0 else None

    upload_id = new_upload_id()
    tmp_path = ensure_inside_root(temp_path_for(upload_id, root), root)
    final_path = ensure_inside_root(final_path_for(upload_id, root), root)

    size_bytes = 0
    finalized = False

    try:
        validate_original_filename(file.filename)
        size_bytes = await _stream_to_temp(file, tmp_path, quota_remaining)

        raw = tmp_path.read_bytes()
        received_sha256 = content_hash(raw)  # skrot pliku w tej postaci, w jakiej przyszedl (XLSX albo CSV)
        max_records = MAX_RECORDS
        if is_xlsx_name(file.filename or ""):
            # parsowanie poza petla zdarzen: odczyt XLSX bywa kosztowny
            raw, _ = await run_in_threadpool(xlsx_import.xlsx_to_csv_bytes, tmp_path, sheet)
            tmp_path.write_bytes(raw)  # od tej chwili plik tymczasowy to CSV
            max_records = xlsx_import.MAX_XLSX_ROWS
        result = validate_csv_bytes(raw, max_records=max_records)

        _enforce_policy_on_path(settings, final_path, root, audit_log)

        os.replace(tmp_path, final_path)
        finalized = True

        df = read_client_csv(str(final_path))
        _, rep = map_dataframe(df)

        audit.record(
            request, action="upload", allowed=True, reason=ReasonCode.OK,
            size_bytes=size_bytes, upload_id=upload_id,
            encoding=result.encoding, content_sha256=received_sha256,
        )

        return UploadResponse(
            upload_id=upload_id,
            filename=final_path.name,
            rows=rep.rows_out,
            columns=[str(c) for c in df.columns],
            mapping=rep.mapping,
            unmapped_columns=rep.unmapped_columns,
            generated=rep.generated,
            warnings=rep.warnings,
            confidence=rep.confidence,
            ready=rep.rows_out > 0,
            run_payload={"pipeline": "ecommerce_demo", "upload_id": upload_id,
                         "client_name": client_name},
        )

    except UploadRejected as rejected:
        audit.record(request, action="upload", allowed=False, reason=rejected.reason, size_bytes=size_bytes)
        raise HTTPException(status_code=rejected.http_status, detail=rejected.message) from None

    except Exception as exc:  # noqa: BLE001
        # Parser moze podniesc wyjatek niosacy tresc pliku klienta w komunikacie —
        # dlatego do odpowiedzi trafia wylacznie komunikat ogolny.
        log.exception("upload processing failed upload_id=%s", upload_id)
        if finalized:
            final_path.unlink(missing_ok=True)
        audit.record(request, action="upload", allowed=False, reason=ReasonCode.PARSER_ERROR,
                     size_bytes=size_bytes)
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


#: Naglowki kazdej odpowiedzi z raportem. Raport jest samodzielnym dokumentem (inline CSS, bez skryptow):
#: CSP zabrania wszystkiego poza stylem inline i obrazami data:. `frame-ancestors 'self'` pozwala osadzic
#: raport w <iframe sandbox> tej samej domeny, dlatego brak X-Frame-Options.
REPORT_HEADERS: dict[str, str] = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _serve_report(path: Path, fmt: str):
    if fmt == "html":
        return HTMLResponse(path.read_text(encoding="utf-8"), headers=REPORT_HEADERS)
    media = "application/json" if fmt == "json" else "text/markdown; charset=utf-8"
    return FileResponse(path, media_type=media, filename=path.name, headers=REPORT_HEADERS)
