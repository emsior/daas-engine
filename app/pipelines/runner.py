"""Orkiestrator pipeline'ów: source -> storage -> transform -> report -> run metadata.

Kontrakt: run() NIGDY nie rzuca wyjątku na zewnątrz. Błąd = RunResult(FAILED).
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.models import DataStatus, PipelineInfo, RunRequest, RunResult, RunStatus
from app.reporting.generator import ReportGenerator
from app.sources.cs2_source import CS2Source, SourceResult
from app.sources.ecommerce_source import EcommerceSource
from app.storage.duckdb_client import DuckDBClient
from app.transforms.cs2_transform import transform_cs2
from app.transforms.ecommerce_transform import transform_ecommerce

log = logging.getLogger(__name__)


PIPELINES: dict[str, dict[str, Any]] = {
    "cs2_demo": {
        "description": "CS2 portfolio pipeline: win rate, K/D, rating, ADR, form score",
        "domain": "esports",
        "required_secrets": ["FACEIT_API_KEY"],
    },
    "ecommerce_demo": {
        "description": "E-Commerce B2B demo: zamówienia, przychód, zysk, marża, anomalie, top kategorie",
        "domain": "ecommerce",
        "required_secrets": ["APIFY_TOKEN"],
    },
}


class PipelineRunner:
    def __init__(self, settings: Settings | None = None, db: DuckDBClient | None = None):
        self.settings = settings or get_settings()
        self.db = db or DuckDBClient(self.settings.duckdb_file)
        self.reports = ReportGenerator(self.settings.reports_path)

    # ------------------------------------------------------------------
    def list_pipelines(self) -> list[PipelineInfo]:
        status = self.settings.secrets_status()
        out = []
        for name, meta in PIPELINES.items():
            present = all(status.get(s, False) for s in meta["required_secrets"])
            out.append(PipelineInfo(
                name=name,
                description=meta["description"],
                domain=meta["domain"],
                required_secrets=meta["required_secrets"],
                secrets_present=present,
                expected_data_status=DataStatus.LIVE_DATA if present else DataStatus.MOCK_DATA,
            ))
        return out

    # ------------------------------------------------------------------
    def run(self, request: RunRequest) -> RunResult:
        run_id = str(uuid.uuid4())
        started = datetime.utcnow()
        t0 = time.perf_counter()
        pipeline = request.pipeline
        log.info("run start pipeline=%s run_id=%s", pipeline, run_id)

        try:
            if self.settings.daas_policy_enforce:
                from app.core.policy import Budget, ReadFile, enforce
                audit_log_path = self.settings.audit_log_path
                if request.source_path:
                    enforce(
                        tool="read_file",
                        raw_args={"path": request.source_path},
                        schema=ReadFile,
                        fn=lambda path: path,
                        budget=Budget(),
                        role="analyst",
                        audit_log=audit_log_path,
                        workspace=self.settings.reports_path.parent,
                    )

            if pipeline == "cs2_demo":
                src, metrics, records = self._run_cs2(run_id, request.force_mock)
            elif pipeline == "ecommerce_demo":
                src, metrics, records = self._run_ecommerce(run_id, request.force_mock, request.source_path)
            else:
                raise ValueError(f"unknown pipeline: {pipeline}")
            if src.data_status == DataStatus.BLOCKED_MISSING_SECRET and not src.records:
                raise FileNotFoundError(src.detail)

            rep = self.reports.write(
                pipeline, run_id, src.data_status, metrics, src.detail, records, started,
                client_name=request.client_name,
            )
            finished = datetime.utcnow()
            result = RunResult(
                run_id=run_id,
                pipeline=pipeline,
                run_status=RunStatus.SUCCESS if records else RunStatus.PARTIAL,
                data_status=src.data_status,
                records_processed=records,
                report_path=str(rep.md),
                report_json_path=str(rep.json),
                report_html_path=str(rep.html),
                narrative_source=rep.narrative_source,
                client_name=request.client_name,
                execution_time_sec=round(time.perf_counter() - t0, 3),
                started_at=started,
                finished_at=finished,
                source_detail=src.detail,
                metrics=_summary(metrics),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("run failed pipeline=%s run_id=%s", pipeline, run_id)
            result = RunResult(
                run_id=run_id,
                pipeline=pipeline,
                run_status=RunStatus.FAILED,
                data_status=DataStatus.BLOCKED_MISSING_SECRET,
                records_processed=0,
                client_name=request.client_name,
                execution_time_sec=round(time.perf_counter() - t0, 3),
                started_at=started,
                finished_at=datetime.utcnow(),
                error=f"{type(exc).__name__}: {exc}",
            )

        self.db.upsert_run(result.model_dump())
        if request.notify:
            self._notify(result)
        log.info("run done pipeline=%s status=%s data=%s records=%s", pipeline, result.run_status.value,
                 result.data_status.value, result.records_processed)
        return result

    # ------------------------------------------------------------------
    def _run_cs2(self, run_id: str, force_mock: bool) -> tuple[SourceResult, Any, int]:
        src = CS2Source(self.settings).fetch(force_mock=force_mock)
        n = self.db.write_cs2_matches(run_id, src.records, src.data_status.value)
        return src, transform_cs2(src.records), n

    def _run_ecommerce(self, run_id: str, force_mock: bool, source_path: str | None = None) -> tuple[SourceResult, Any, int]:
        src = EcommerceSource(self.settings).fetch(force_mock=force_mock, source_path=source_path)
        n = self.db.write_ecommerce_orders(run_id, src.records, src.data_status.value)
        return src, transform_ecommerce(src.records), n

    # ------------------------------------------------------------------
    def _notify(self, result: RunResult) -> None:
        if not self.settings.has_discord:
            log.info("DISCORD_WEBHOOK_URL missing -> notification skipped")
            return
        content = (
            f"**DaaS run** `{result.pipeline}` → {result.run_status.value} / {result.data_status.value}\n"
            f"records: {result.records_processed} · {result.execution_time_sec}s · run_id `{result.run_id[:8]}`"
        )
        try:
            httpx.post(self.settings.discord_webhook_url, json={"content": content}, timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("discord notify failed: %s", exc)


def _summary(metrics: Any) -> dict[str, Any]:
    """Kompaktowe metryki do odpowiedzi API (pełne w raporcie JSON)."""
    d = metrics.model_dump() if hasattr(metrics, "model_dump") else dict(metrics)
    keep = [k for k, v in d.items() if not isinstance(v, (list, dict))]
    out = {k: d[k] for k in keep}
    if "wow" in d:                       # e-commerce: delta tydzień-do-tygodnia jest mała i kluczowa dla alertów w n8n
        out["wow"] = d["wow"]
        out["weeks_in_data"] = len(d.get("weekly") or [])
    return out
