"""Endpointy FastAPI.

Dwa routery:
    * `public_router` — `/health` i `/ready` (zredagowane, bez klucza),
    * `router`        — wszystko inne, z zaleznoscia `require_api_key` na calym routerze.
Nowa trasa dodana do `router` jest chroniona automatycznie; test `tests/test_auth.py` pilnuje reszty.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy import text

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
from app.core.observability import get_request_id
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
    remove_quietly,
    temp_path_for,
    validate_csv_bytes,
    validate_original_filename,
)
from app.pipelines.runner import PIPELINES, PipelineRunner
from app.sources.xlsx_formulas import formula_warning

log = logging.getLogger(__name__)


def read_report_bytes(path: Path) -> tuple[bytes, str]:
    """Jeden odczyt pliku raportu: `(bajty, sha256 tych samych bajtow)`.

    Hash liczony z serwowanych bajtow jest zgodny z `sha256sum` klienta i z `report_sha256`/ETag liczonym w cloud
    (ta sama funkcja uzywana przez `app.api.v1.runs`) — raport i jego skrot nie moga sie rozjechac.
    """
    body = path.read_bytes()
    return body, hashlib.sha256(body).hexdigest()


#: Nazwa naglowka ze skrotem raportu na odpowiedziach legacy `GET /reports/...` (F1: "sprawdz sam" w selfhost REST).
REPORT_SHA256_HEADER = "X-Report-SHA256"


class RunResultWithHash(RunResult):
    """Odpowiedz legacy `POST /run` = `RunResult` + sha256 raportu HTML (parytet z `report_sha256` w cloud)."""

    report_sha256: str | None = None


#: Prefiksy tras legacy (tylko selfhost); dostaja naglowek `Deprecation`.
LEGACY_PREFIXES: tuple[str, ...] = ("/pipelines", "/run", "/runs", "/stats", "/upload", "/reports")

public_router = APIRouter()
# kolejnosc ma znaczenie: limit per IP (takze dla anonimowych), potem klucz API
router = APIRouter(dependencies=[Depends(limit_ip), Depends(require_api_key)])


def _runner(request: Request) -> PipelineRunner:
    return request.app.state.runner


@public_router.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> HealthResponse:
    """Liveness. Publiczny i minimalny: status, wersja, tryb. Bez sciezek, konfiguracji i sekretow."""
    s = request.app.state.settings
    return HealthResponse(app=s.app_name, version=installed_version(s.app_version), mode=s.daas_mode,
                          timestamp=datetime.utcnow())


def _probe_runtime_dir(directory: Path) -> None:
    """Sprawdza, ze katalog runtime jest zapisywalny (tworzy i usuwa plik probny)."""
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / f".ready-{os.getpid()}"
    probe.write_bytes(b"ok")
    probe.unlink()


@public_router.get("/ready", response_model=ReadyResponse, tags=["system"], responses={503: {"model": ReadyResponse}})
async def ready(request: Request):
    """Readiness: baza control plane odpowiada (`SELECT 1`), katalog runtime jest zapisywalny, a w trybie
    selfhost takze baza organizacji `default`. Przy bledzie 503 bez szczegolow (szczegoly w logu)."""
    state = request.app.state
    try:
        async with state.db_sessionmaker() as db:
            await db.execute(text("SELECT 1"))
        await run_in_threadpool(_probe_runtime_dir, state.settings.runtime_dir)
        if state.runner is not None:
            await run_in_threadpool(state.runner.db.table_counts)
    except Exception:  # noqa: BLE001
        log.exception("readiness check failed")
        return JSONResponse(ReadyResponse(status="not_ready").model_dump(), status_code=503)
    return ReadyResponse(status="ready")


@router.get("/pipelines", response_model=list[PipelineInfo], tags=["pipelines"])
def pipelines(request: Request) -> list[PipelineInfo]:
    return _runner(request).list_pipelines()


def _run_validation_response(errors: list[dict]) -> JSONResponse:
    """422 walidacji ciala legacy `/run` w ksztalcie pozostalych bledow legacy (`detail`/`code`/`request_id`),
    ale BEZ wartosci przeslanej przez klienta (D12 / API.md: "the submitted value is never echoed")."""
    return JSONResponse({"detail": errors, "code": "validation_error", "request_id": get_request_id()},
                        status_code=422)


async def _read_run_request(request: Request) -> RunRequest | JSONResponse:
    """Czyta i waliduje cialo `POST /run` tutaj (a nie przez model w sygnaturze), zeby 422 nie odbijal wartosci
    klienta. Zwraca `RunRequest` albo gotowa odpowiedz 422 (loc/msg/type, bez `input`)."""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001  niepoprawny JSON: zglaszamy blad bez cytowania tresci
        return _run_validation_response([{"loc": ["body"], "msg": "request body is not valid JSON",
                                          "type": "value_error.jsondecode"}])
    try:
        return RunRequest.model_validate(payload)
    except ValidationError as exc:
        return _run_validation_response(
            [{"loc": ["body", *(str(x) for x in e.get("loc", ()))], "msg": e.get("msg"), "type": e.get("type")}
             for e in exc.errors()])


@router.post("/run", response_model=RunResultWithHash, tags=["pipelines"], dependencies=[Depends(limit_run)])
async def run(request: Request):
    body = await _read_run_request(request)
    if isinstance(body, JSONResponse):   # walidacja ciala nie przeszla -> 422 bez echa
        return body
    if body.pipeline not in PIPELINES:
        raise HTTPException(status_code=404, detail=f"unknown pipeline: {body.pipeline}")
    runner = _runner(request)
    if body.upload_id:
        # plik z POST /upload: sciezke sklada serwer z identyfikatora (wzorzec ^[0-9a-f]{32}$), nie klient
        path = _uploads_root(runner.settings) / f"{body.upload_id}.csv"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="upload not found")
        body = body.model_copy(update={"source_path": str(path), "upload_id": None})
    result = await run_in_threadpool(runner.run, body)
    audit.record(
        request, action="run", allowed=True,
        reason=ReasonCode.RUN_FAILED if result.run_status.value == "FAILED" else ReasonCode.OK,
        pipeline=result.pipeline, run_id=result.run_id,
    )
    # F1: skrot raportu HTML (ten sam, ktory liczy cloud) w polu odpowiedzi — klient selfhost ma referencje do
    # "sprawdz sam"; sciezka jest wewnetrzna (realna), odpowiedz zredagowana oddaje tylko link.
    report_sha256 = None
    if result.report_html_path and Path(result.report_html_path).is_file():
        report_sha256 = read_report_bytes(Path(result.report_html_path))[1]
    return RunResultWithHash(**redact_run(result).model_dump(), report_sha256=report_sha256)


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
    root = settings.uploads_path
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
        formula_note: str | None = None
        if is_xlsx_name(file.filename or ""):
            # parsowanie poza petla zdarzen: odczyt XLSX bywa kosztowny
            raw, _ = await run_in_threadpool(xlsx_import.xlsx_to_csv_bytes, tmp_path, sheet)
            # C14: skan formul na zwalidowanym XLSX, przed nadpisaniem pliku tymczasowego konwersja CSV
            formula_note = await run_in_threadpool(formula_warning, tmp_path, sheet)
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
            warnings=[*rep.warnings, formula_note] if formula_note else rep.warnings,
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
            remove_quietly(final_path)
        audit.record(request, action="upload", allowed=False, reason=ReasonCode.PARSER_ERROR,
                     size_bytes=size_bytes)
        raise HTTPException(status_code=422, detail="nie udalo sie przetworzyc pliku") from exc

    finally:
        # Plik tymczasowy nie moze przetrwac zadnej sciezki wyjscia.
        remove_quietly(tmp_path)


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
    # F1: sha256 serwowanych bajtow w naglowku, zeby klient selfhost mogl "sprawdzic sam" (README) tak jak w cloud.
    body, digest = read_report_bytes(path)
    headers = {**REPORT_HEADERS, REPORT_SHA256_HEADER: digest}
    if fmt == "html":
        return HTMLResponse(body, headers=headers)
    media = "application/json" if fmt == "json" else "text/markdown; charset=utf-8"
    return FileResponse(path, media_type=media, filename=path.name, headers=headers)
