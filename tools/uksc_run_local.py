"""Uruchom pipeline uksc_evidence lokalnie na paczce z collectora, bez serwera HTTP.

python tools/uksc_run_local.py <plik.json | katalog z uksc_*.json> [--client "Nazwa klienta"]

Kopiuje paczke do runtime/uploads/ (zrodlo akceptuje tylko ten katalog), odpala PipelineRunner
i wypisuje status, metryki i sciezke raportu HTML. Uzywa ustawien z .env (DUCKDB_PATH, REPORTS_DIR).
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="plik uksc_*.json albo katalog z paczkami (bierze najnowsza)")
    ap.add_argument("--client", default=None)
    args = ap.parse_args()

    from app.core.config import Settings, reset_settings_cache
    from app.core.models import RunRequest
    from app.pipelines.runner import PipelineRunner
    from app.storage.duckdb_client import DuckDBClient

    p = Path(args.path)
    if p.is_dir():
        cands = sorted(glob.glob(str(p / "uksc_*.json")), key=os.path.getmtime)
        if not cands:
            print(f"BRAK PACZKI uksc_*.json w {p}")
            return 2
        p = Path(cands[-1])
    if not p.exists():
        print(f"BRAK PLIKU: {p}")
        return 2

    reset_settings_cache()
    s = Settings()
    s.ensure_dirs()
    uploads = s.reports_path.parent / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    dst = uploads / p.name
    shutil.copy(p, dst)

    runner = PipelineRunner(settings=s, db=DuckDBClient(s.duckdb_file))
    res = runner.run(RunRequest(pipeline="uksc_evidence", source_path=str(dst), client_name=args.client))
    print(f"run: {res.run_status.value} {res.data_status.value} records={res.records_processed} error={res.error}")
    keys = ("coverage_pct", "pass_pct", "passed", "failed", "warned", "manual", "na_no_admin", "errors")
    print("metrics:", {k: res.metrics.get(k) for k in keys})
    print("RAPORT HTML:", res.report_html_path)
    return 0 if res.run_status.value != "FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
