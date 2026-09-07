"""Endpointy FastAPI: /health, /pipelines, /run, /runs/latest (+ /runs, /runs/{id})."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from app.core.models import HealthResponse, PipelineInfo, RunRequest, RunResult, UploadResponse
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
ALLOWED_EXT = {".csv", ".txt", ".tsv", ".xlsx", ".xls"}
MAX_UPLOAD_MB = 25


@router.post("/upload", response_model=UploadResponse, tags=["pipelines"])
async def upload(request: Request, file: UploadFile = File(...),
                 client_name: str | None = Form(default=None)) -> UploadResponse:
    """Przyjmuje CSV/XLSX klienta, zapisuje w runtime/uploads/, zwraca wykryte mapowanie kolumn
    oraz gotowy payload do POST /run (source_path). Nic nie liczy — to tylko walidacja wejścia."""
    from app.sources.csv_mapper import map_dataframe, read_client_csv

    ext = Path(file.filename or "upload.csv").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=415, detail=f"unsupported file type {ext}; allowed: {sorted(ALLOWED_EXT)}")
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"file too large (> {MAX_UPLOAD_MB} MB)")
    if not raw.strip():
        raise HTTPException(status_code=400, detail="empty file")

    settings = _runner(request).settings
    uploads_dir = settings.reports_path.parent / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex[:12]
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file.filename or "upload").name)
    dest = uploads_dir / f"{upload_id}_{safe_name}"
    dest.write_bytes(raw)

    try:
        df = pd.read_excel(dest, dtype=str) if ext in {".xlsx", ".xls"} else read_client_csv(str(dest))
        _, rep = map_dataframe(df)
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"cannot parse file: {type(exc).__name__}: {exc}") from exc

    return UploadResponse(
        upload_id=upload_id,
        source_path=str(dest),
        filename=file.filename or safe_name,
        rows=rep.rows_out,
        columns=[str(c) for c in df.columns],
        mapping=rep.mapping,
        unmapped_columns=rep.unmapped_columns,
        generated=rep.generated,
        warnings=rep.warnings,
        confidence=rep.confidence,
        ready=rep.rows_out > 0,
        run_payload={"pipeline": "ecommerce_demo", "source_path": str(dest), "client_name": client_name},
    )


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
