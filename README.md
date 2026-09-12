# DaaS Engine — automated weekly reports from any client dataset

Source-agnostic **Data-as-a-Service** engine. A client drops in a CSV/XLSX export; the engine detects the column mapping, loads it into DuckDB, computes KPIs and anomalies, and returns a ready-to-send HTML report with week-over-week deltas and a plain-language summary.

**Stack:** Python 3.11 · FastAPI · DuckDB · n8n · Docker

*[Polski README →](README.pl.md)*

![HTML report generated from a client file (client_export_pl.csv): KPIs, margin, loss-making orders, anomalies and a "practical takeaways" section](docs/demo_report.png)

---

## Why it exists

Small e-commerce businesses sit on export files they never analyse. Reporting tools either need a data engineer to set up or a per-seat subscription nobody wants for a 15-minute weekly question: *did we make money last week, and where did we lose it?*

DaaS Engine answers that from a raw file, with no schema agreed in advance.

**Design constraints that shaped it:**

- **A missing API key is not an error.** Every external source degrades to a fixture instead of failing. The pipeline always produces a report; the reason for any degradation is returned in `source_detail`.
- **Source-agnostic ingestion.** Column names are detected, not configured — `Wartość brutto`, `gross_value` and `Revenue` all map to `revenue`.
- **The report is the product.** Output is a self-contained HTML file a client can open, not a dashboard they have to log into.

---

## Architecture

```
app/
├── api/          FastAPI routes
├── core/         config, Pydantic models
├── sources/      ingestion: FACEIT, Apify, CSV/XLSX column mapper
├── transforms/   metric computation per domain
├── storage/      DuckDB client
├── pipelines/    orchestration (runner)
└── reporting/    HTML / Markdown / JSON generators + narrative
```

Two reference pipelines ship with the engine:

| Pipeline | Domain | Source |
|---|---|---|
| `ecommerce_demo` | orders, revenue, margin | Apify, CSV/XLSX upload, or fixture |
| `cs2_demo` | esports match performance | FACEIT API or fixture |

---

## Quickstart — Docker

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL |
|---|---|
| API + Swagger | `http://localhost:8000/docs` |
| n8n | `http://localhost:5678` |

The DuckDB database and generated reports land in `./runtime/` (host volume).

Import a workflow in n8n via **Workflows → Import from file**:
`n8n/daas_workflow.json` (scheduled run) or `n8n/daas_client_report.json` (client file → report).

## Quickstart — Windows, no Docker

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

If `Activate.ps1` is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

---

## The "client file → report" flow

```bash
# 1. Upload — returns the detected column mapping and a ready run_payload
curl -F "file=@client_export_pl.csv" http://localhost:8000/upload
# {"rows":15,"confidence":1.0,"ready":true,
#  "mapping":{"revenue":"Wartość brutto", ...},
#  "run_payload":{"pipeline":"ecommerce_demo","source_path":"...","client_name":"Sklep Demo"}}

# 2. Run the pipeline with the payload from step 1
curl -X POST http://localhost:8000/run \
     -H "Content-Type: application/json" \
     -d '{"pipeline":"ecommerce_demo","source_path":"...","client_name":"Sklep Demo"}'

# 3. Fetch the report (fmt: html | md | json)
curl "http://localhost:8000/reports/ecommerce_demo/latest?fmt=html"
```

![n8n workflow: webhook → POST /upload → validation → POST /run → response with a link to the report](docs/n8n_client_workflow.png)

---

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Service status, version, secret availability map |
| `GET` | `/pipelines` | Available pipelines and their data status |
| `POST` | `/run` | Execute a pipeline (`pipeline`, `force_mock`, `notify`) |
| `POST` | `/upload` | Accept CSV/XLSX, return detected column mapping |
| `GET` | `/runs/latest` | Most recent run, optionally filtered |
| `GET` | `/runs?limit=20` | Run history |
| `GET` | `/runs/{run_id}` | A specific run |
| `GET` | `/reports/{pipeline}/latest` | Latest report — `fmt=html\|md\|json` |
| `GET` | `/reports/run/{run_id}` | Report for a specific run |
| `GET` | `/stats` | DuckDB table row counts |

---

## Metrics

**`ecommerce_demo`**
`revenue_total`, `cost_total`, `profit_total`, `margin_pct`, `avg_order_value`, `avg_discount_pct`, `loss_orders` (orders with negative profit), `top_categories`, `top_products`, ISO-week aggregation and week-over-week deltas (revenue %, profit %, margin in percentage points).

Anomaly detectors: `NEGATIVE_PROFIT`, `DEEP_DISCOUNT`, `MARGIN_OUTLIER`, `REVENUE_OUTLIER`.

**`cs2_demo`**
`win_rate`, `kd_ratio`, `avg_rating`, `avg_adr`, `hs_pct`, `form_score` (0–100, weighted over the last 5 matches), `form_trend` (UP/FLAT/DOWN), and a per-map breakdown with best and worst map.

---

## Configuration and fallback behaviour

All environment variables are optional. Without any of them the engine runs on fixtures and still produces a full report.

| Variable | Used by | Fallback when absent or failing |
|---|---|---|
| `FACEIT_API_KEY`, `FACEIT_PLAYER_NICKNAME` | `cs2_demo` | fixture `cs2_matches.json` → `MOCK_DATA` |
| `APIFY_TOKEN`, `APIFY_DATASET_ID` | `ecommerce_demo` | `ECOMMERCE_CSV_PATH`, then fixture `ecommerce_orders.csv` → `MOCK_DATA` |
| `ECOMMERCE_CSV_PATH` | `ecommerce_demo` | fixture |
| `ANTHROPIC_API_KEY` | report narrative | deterministic template (`narrative_source: TEMPLATE`) |
| `DISCORD_WEBHOOK_URL` | `/run?notify=true` | notification skipped, run still succeeds |
| `N8N_BASIC_AUTH_USER` / `_PASSWORD` | n8n | — |
| `DAAS_PUBLIC_URL` | report links behind a reverse proxy | localhost |

Network failures — timeouts, `401`, upstream schema changes — trigger the same fallback path. The pipeline does not crash; the cause is always reported in `source_detail`.

---

## Tests

```bash
pytest
```

`tests/test_pipelines.py` covers pipeline orchestration and metric computation; `tests/test_client_upload.py` covers column detection and the upload → run flow. CI runs on every push (`.github/workflows/ci.yml`).

See [VALIDATION.md](VALIDATION.md) for the manual validation checklist and known limitations.
