# Architecture

One codebase, two modes. A FastAPI process runs the pipelines; a control plane keeps accounts and metadata; each customer organization has its own data directory and its own DuckDB file. Sources degrade to fixtures, so every run ends with a report.

## Modes (`DAAS_MODE`)

| | `selfhost` | `cloud` |
|---|---|---|
| Organizations | one, `default`, created at start | many, created in the cockpit or with `python -m app.cli create-org` |
| Credentials | one instance key, `DAAS_API_KEY` | per-organization API keys and magic-link sessions |
| Routes | `/api/v1` plus legacy `/upload`, `/run`, `/reports/...` (with `Deprecation` header) | `/api/v1` only; legacy routes answer `404` |
| Runs | legacy `/run` is synchronous; v1 jobs are asynchronous | jobs only (`202`, then poll or SSE) |
| Cockpit | optional | `web/` (Next.js) |
| Typical deployment | `docker-compose.yml` at the repository root (with n8n) or `pip install` | `deploy/compose.yml` on a VPS |

The mode changes which routes and credentials exist; pipelines, reports and the data plane are the same code.

## Control plane and data plane

```
                         ┌──────────────────────────── one process (workers = 1) ───────────────────────────┐
 browser ──► web (Next.js)│                                                                                  │
   │         cockpit      │  FastAPI app/main.py                                                              │
   │ /api/*               │   middleware: request id, security headers, metrics                               │
   └──────────────────────┼──► api/v1 routers ──► auth: key or session ──► org_access(scope) ──┐              │
 API client ──X-API-Key───┼──►                                                                  │              │
                          │                                                                    ▼              │
                          │   CONTROL PLANE  app/control/ (SQLAlchemy async, Alembic)      DATA PLANE         │
                          │   control.db (SQLite; Postgres optional)                       app/control/tenancy│
                          │   orgs, users, memberships, api_keys (hash),                   runtime/tenants/   │
                          │   sessions, magic_links, invitations, uploads (metadata),        <org_id>/        │
                          │   jobs, run_records (report hashes), audit_events,                 daas.duckdb    │
                          │   usage_counters, webhooks                                         uploads/       │
                          │          ▲                                                         reports/       │
                          │          │ state, results                                          audit.jsonl    │
                          │   JobExecutor (queue, 1 run per org, global semaphore) ───────────► PipelineRunner │
                          │          │                                                                         │
                          │          └─► listeners: usage, audit, webhook delivery (HMAC)                      │
                          └────────────────────────────────────────────────────────────────────────────────────┘
        outbound only: SMTP (sign-in links), webhooks you register, optional LLM provider
```

**Control plane** (`app/control/`): who may do what, and the trace of what happened. Models in `models.py`, migrations in `alembic/versions/` (`0001_initial` the schema, `0002_run_period` the data period of a run, `0003_retention_loginnonce_indexes` the report-purge marker, the sign-in link nonce and listing indexes; applied at start with `DAAS_AUTO_MIGRATE`, or by hand with `python -m app.cli migrate`). It stores metadata only: no customer file or report content. API keys, sign-in tokens and invitation tokens exist only as SHA-256 hashes. `RunRecord` keeps the SHA-256 of each report so `POST .../verify` can answer "is this one of ours" without reading the report.

**Data plane** (`app/control/tenancy.py`): everything that belongs to a customer, in one directory per organization.

```
runtime/
├── control.db                       control plane (cloud deployments keep it in its own volume: /data/control)
├── outbox/                          sign-in links of the console mail adapter (trials only)
└── tenants/<org_id>/
    ├── daas.duckdb                  metrics and run history of this organization (one writer per file)
    ├── uploads/<upload_id>.csv|.json  files received (XLSX is converted to CSV); removed after `retention_until` (set per upload from the organization's retention)
    ├── reports/                     html, md and json reports; files removed after `Org.retention_days` (the run record and its hashes stay)
    └── audit.jsonl                  technical audit lines of this organization
```

`TenantPaths.for_org` validates the id (no separators or dots) and refuses a directory outside `tenants/`. `TenantRegistry` opens a runner per organization lazily and keeps at most `DAAS_MAX_ACTIVE_TENANTS` open (LRU; an organization with a run in progress is never closed). That is a limit on the **number of open tenants** (file handles and DuckDB instances), not on memory: the RAM a single run needs is bounded by the upload size limit and the container limit (`mem_limit` in `deploy/compose.yml`), not by this setting. A per-organization `asyncio.Lock` serializes writes to its DuckDB file. In `selfhost` the same layout is used for `default`; `python -m app.cli migrate-runtime` moves a 1.x `runtime/` into it (idempotent, nothing is overwritten).

### Job executor (`app/control/executor.py`)

`POST .../jobs` writes a `Job` (`queued`) to the control database and answers `202` with a `Location`. The executor, started with the application, takes jobs from its queue and runs the pipeline in a worker thread:

- at most one run per organization at a time, at most `DAAS_MAX_CONCURRENT_RUNS` in the process;
- state lives in the database, not in memory: after a restart `running` jobs become `failed` (`abandoned`) and `queued` jobs are queued again;
- a `queued` job can be cancelled; a running one finishes;
- on completion it stores the `RunRecord` with report hashes, increments the monthly usage counter, writes the audit event and notifies listeners (webhook delivery);
- progress is published to an in-process bus; `GET .../events` turns it into Server-Sent Events (`status`, `progress`, `log`, `done`).

### Retention (`app/control/retention.py`)

In `cloud` mode a background task started with the application (every `DAAS_RETENTION_INTERVAL_MIN` minutes, `0` = off; `python -m app.cli purge [--dry-run] [--org ID]` does the same on demand) removes, per organization:

- uploads after `Upload.retention_until` (file and record; an upload that a queued or running job still reads is skipped until the job ends);
- report files of runs older than `Org.retention_days` (`RunRecord` and hashes stay, `POST .../verify` keeps working, the report endpoint answers `410 report_expired` via the `report_purged_at` marker);
- the tenant's DuckDB rows (`runs` and the run-keyed data tables) older than the same period.

Each deletion is audited (`upload.purged`, `report.purged`) without customer file names. The operation is idempotent and takes the per-organization lock, so it never overlaps a run of that organization. Backups (`deploy/backup.sh`) are not touched by retention: see [`SECURITY.md`](SECURITY.md).

## Request path

```
request ─► RequestIdMiddleware ─► SecurityHeadersMiddleware ─► (CORS only for DAAS_COCKPIT_ORIGIN)
        ─► limit_ip (token bucket per address)
        ─► get_principal      X-API-Key  or  session cookie (+ CSRF header for mutations)
        ─► org_access(scope)  organization from the credential; foreign org → 404; missing scope → 403 + audit
        ─► handler            plan_guard on POST jobs/uploads (429 plan_limit)
        ─► problem+json on errors (code, request_id)
```

## Pipelines

| Pipeline | Source | Transform | Report |
|---|---|---|---|
| `ecommerce_demo` | `ecommerce_source.py` (upload CSV/XLSX, Apify, fixture) | `ecommerce_transform.py` | `generator.py` |
| `cs2_demo` | `cs2_source.py` (public API, fixture): esports statistics | `cs2_transform.py` | `generator.py` |
| `uksc_evidence` | `uksc_source.py`: JSON package from the local collector `tools/uksc-collector.ps1` | `uksc_transform.py` | `uksc_report.py` + `uksc_pl.py` |

`ecommerce_demo` computes revenue, cost, margin, order value, loss-making orders, anomalies and week-over-week deltas from a client export; column names are detected (`sources/csv_mapper.py`, Polish and English aliases). `uksc_evidence` is the collector-and-status-report vertical: a read-only collector on the customer's workstation produces a hashed JSON package; the report lists control, requirement, status and artifact hash with the package SHA-256, and the cockpit page "evidence" shows the same table. The pipeline name `uksc_evidence` is part of the API contract.

Data flow of one run:

```
upload ─► core/upload_guard (name, size, quota, content, audit)
      └─► core/xlsx_import (XLSX only: container, macro, sheet limits → CSV)
      └─► sources/csv_mapper → run payload
job   ─► pipelines/runner (PipelineRunner)
           ├─ sources/*        live data → fixture fallback (reason in source_detail)
           ├─ transforms/*     pandas metrics, ISO weeks, deltas
           ├─ storage/duckdb_client   tables per pipeline in the organization's DuckDB
           └─ reporting/*      HTML / Markdown / JSON + summary (template, or LLM on opt-in)
      ─► reports/ in the tenant directory, SHA-256 into RunRecord
```

## Cockpit (`web/`)

Next.js 16 (App Router, React 19, TypeScript strict, Tailwind 4, TanStack Query). No external CDN or fonts. `src/proxy.ts` sets a nonce-based CSP and redirects to `/login` when `GET /api/v1/me` answers `401`. The browser talks to the API at the same origin (`/api/*`, through Caddy) with the session cookie and the CSRF header; the report is shown in a sandboxed iframe; the "check yourself" box computes SHA-256 locally in the browser (Web Crypto) and sends nothing. Screens: login, onboarding (DPA), dashboard, upload wizard with live progress, run/report, evidence, API keys, members, settings. Details: [`web/README.md`](../web/README.md).

## Deployment

`deploy/compose.yml`: `api` (image from the root `Dockerfile`), `web` (image from `web/Dockerfile`), optional `caddy` (profile `edge`), volumes `control` and `runtime`. Instructions, update, rollback and backups: [`DEPLOY.md`](DEPLOY.md) and [`deploy/README.md`](../deploy/README.md). Threat model: [`SECURITY.md`](SECURITY.md). HTTP contract: [`API.md`](API.md) and [`openapi.json`](openapi.json).

Constraints that shape the design: one process (DuckDB allows one writer per file and the rate limiters are in memory, so the server refuses more than one worker), one node, SQLite control database by default.

## Tests (`tests/`, all offline)

| File | Covers |
|---|---|
| `test_pipelines.py`, `test_client_upload.py` | orchestration, metrics, column mapper, upload → run → report |
| `test_upload_security.py`, `test_security.py`, `test_xlsx_upload.py` | upload guard, path traversal, secrets, XLSX limits |
| `test_auth.py`, `test_auth_v1.py`, `test_limits.py` | key on every legacy route, v1 authentication, rate limits and quota |
| `test_control_plane.py`, `test_tenancy.py`, `test_migrate_runtime.py`, `test_mode_selfhost.py` | models and migrations, tenant paths and registry, runtime migration, selfhost mode |
| `test_orgs_v1.py`, `test_jobs.py`, `test_verify.py`, `test_usage_audit.py`, `test_webhooks.py`, `test_metrics.py` | organizations, members, keys, uploads, jobs and SSE, hash verification, usage and audit, signed webhooks, Prometheus |
| `test_report_xss.py`, `test_redaction.py`, `test_narrative.py`, `test_observability.py` | inert report output, redacted responses, LLM opt-in, request tracing and headers |
| `test_members_admin.py`, `test_invitations.py`, `test_magic_link_csrf.py`, `test_console_mail_guard.py`, `test_retention.py`, `test_sse_limits.py`, `test_runs.py`, `test_runs_api.py` | member and invitation management, sign-in link confirmation, console mail guard, retention, SSE limits, report endpoint (410, ETag) |
| `test_policy.py`, `test_policy_runtime.py`, `test_audit_log_unification.py`, `test_uksc_pipeline.py` | policy layer, single audit path, collector pipeline |
| `test_hardening_defaults.py`, `test_consumers.py`, `test_openapi_export.py` | secure defaults and `.env.example`, n8n/CI/Docker consumers and locks, `docs/openapi.json` freshness |
| `test_postgres.py` | PostgreSQL only (skipped without `DAAS_TEST_POSTGRES_URL`): migrations up/down/up against the models, the owner guard (`FOR UPDATE`) with two concurrent sessions, the usage-counter SAVEPOINT race |

Web: `npm test` (Vitest) and `npm run e2e` (Playwright against the real API in cloud mode with the console mail adapter; Chromium). `npm run e2e:firefox` and `npm run e2e:webkit` run the same suite in Firefox and WebKit; WebKit needs the stack over https (`E2E_TLS=1`) because it refuses `Secure` cookies over plain http.

## CI (`.github/workflows/ci.yml`)

`test` (Python 3.11, 3.12, 3.13: OPSEC grep, ruff, pytest, API smoke), `postgres` (`tests/test_postgres.py` on the PostgreSQL 16 of the `ubuntu-24.04` runner), `security` (gitleaks, semgrep, zizmor, pip-audit of `requirements.lock` and `requirements-postgres.lock`), `web` (gen:api drift, eslint, tsc, vitest, `next build`, `npm audit` of production dependencies), `e2e` (Playwright, Chromium), `e2e-browsers` (the same suite in Firefox over http and WebKit over https), `lighthouse` (mobile, `/login` and `/dpa`: performance >= 0.9, accessibility >= 0.95, best practices >= 0.9), `docker` (API and web image builds, container smoke in selfhost and cloud mode with a read-only file system, compose and Caddyfile validation).
