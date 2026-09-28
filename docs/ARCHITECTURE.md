# Architecture

One FastAPI process, one DuckDB file, one deployment per client. Sources degrade to fixtures, so every run ends with a report.

## Data flow

```
client CSV ─► POST /upload ─► core/upload_guard   name, 10 MiB streamed limit, content, audit event
                   │            └─► core/policy     enforce() on the final path (DAAS_POLICY_ENFORCE)
                   ▼
            sources/csv_mapper  detect PL/EN column names ─► run_payload
                   ▼
POST /run ─► pipelines/runner (PipelineRunner)
               ├─ sources/*       LIVE data → fixture fallback (reason in source_detail)
               ├─ transforms/*    pandas metrics, ISO weeks, week-over-week deltas
               ├─ storage/duckdb_client   DuckDB tables per pipeline
               └─ reporting/*     HTML / Markdown / JSON + narrative (LLM or template)
                   ▼
GET /reports/{pipeline}/latest?fmt=html|md|json
```

n8n (`n8n/*.json`) calls the same HTTP endpoints on a schedule or from a webhook.

## Pipelines

| Pipeline | Source | Transform | Markdown report |
|---|---|---|---|
| `ecommerce_demo` | `ecommerce_source.py` (upload, Apify, fixture) | `ecommerce_transform.py` | `generator.py` |
| `cs2_demo` | `cs2_source.py` (FACEIT API, fixture) | `cs2_transform.py` | `generator.py` |
| `uksc_evidence` | `uksc_source.py` (package from `tools/uksc-collector.ps1`, fixture) | `uksc_transform.py` | `uksc_report.py` + `uksc_pl.py` |

`reporting/generator.py` renders every Markdown report to HTML via `reporting/html.py`. Registry: `PIPELINES` in
`app/pipelines/runner.py`. UKSC package format: `schemas/uksc_collector_v1.json`; `tools/uksc_run_local.py` runs it without the server.

## Endpoints (`app/api/routes.py`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | status, version, secret availability |
| `GET` | `/pipelines` | pipelines and their data status |
| `POST` | `/upload` | accept a client CSV, return the detected mapping and `run_payload` |
| `POST` | `/run` | execute a pipeline |
| `GET` | `/runs`, `/runs/latest`, `/runs/{run_id}` | run history |
| `GET` | `/reports/{pipeline}/latest`, `/reports/run/{run_id}` | reports |
| `GET` | `/stats` | DuckDB row counts |

## Tests (`tests/`, 195 in total, all offline)

| File | Covers |
|---|---|
| `test_pipelines.py` | orchestration, metrics, endpoint status codes |
| `test_client_upload.py` | column mapper, upload → run → HTML flow |
| `test_upload_security.py`, `test_security.py` | upload guard, path traversal, secret handling |
| `test_csv_ingest_perf.py` | CSV parsing paths and separators |
| `test_policy.py`, `test_policy_runtime.py` | policy layer and its runtime wiring |
| `test_audit_log_unification.py` | single audit log path, concurrent writes |
| `test_uksc_pipeline.py` | UKSC package validation, scoring, report |

CI (`.github/workflows/ci.yml`): `test` on Python 3.11 and 3.12 (OPSEC grep, ruff, pytest, API smoke),
`security` (gitleaks, semgrep, zizmor), `docker` (image build and smoke).
