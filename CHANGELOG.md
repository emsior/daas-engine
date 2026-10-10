# Changelog

All notable changes to DaaS Engine. Dates are merge dates (Europe/Warsaw).

## [2.0.0] — Unreleased

Hardening and cloud release. One code base, two modes (`DAAS_MODE=selfhost|cloud`). Behaviour changes in
the hardening sections are breaking for existing deployments: configure `DAAS_API_KEY` and update API
clients before upgrading. The `cloud` section is additive; `selfhost` keeps the existing routes.

### Changed

- All routes except `GET /health` and `GET /ready` require the `X-API-Key` header. Without a configured key the
  protected routes answer `401`, and the server refuses to start on a non-loopback address.
- The default listen address is `127.0.0.1`; the container image sets `0.0.0.0` explicitly and Compose publishes ports on loopback only.
- The policy layer is on by default (`DAAS_POLICY_ENFORCE=true`).
- `POST /run` takes `upload_id` instead of a file path. `GET /health` returns a redacted status (no paths, no configuration); `/docs`, `/redoc`
  and `/openapi.json` exist only with `APP_ENV=dev`.
- API responses no longer contain absolute paths or raw exception text. Errors are `{detail, code, request_id}`.
- The LLM summary is opt-in (`DAAS_LLM_NARRATIVE=true`); an API key alone does not enable it. Identifying fields are removed from the request and the report labels the summary as model-generated.
- The audit log records `upload`, `run` and `auth_denied` events with request id and client address.
- Version bumped to 2.0.0.

### Added

- In-memory token-bucket rate limits for `/run`, `/upload`, per-IP traffic and failed keys (`429` with `Retry-After`), a 1 GiB upload quota (`413`) and cleanup of stale `*.tmp` files.
- XLSX support in `POST /upload` with container, macro, embedded-object, sheet and size limits; a workbook with several sheets needs the `sheet` field.
- Request id, JSON logging and security headers on every response; report responses add a restrictive `Content-Security-Policy`.
- A startup check that refuses more than one worker.
- `requirements.lock` and `requirements-dev.lock` with SHA-256 hashes; Docker and CI install with `--require-hashes`.
- Tests: an auth matrix over all routes, endpoint-level isolation, redaction, report output, limits, XLSX and consumers.

### Fixed

- Client-supplied values are escaped before they reach HTML reports in all three report builders; the HTML renderer drops raw HTML, links and images.
- n8n workflows, the CI smoke test, the container health check and the PowerShell launcher send the API key.
- Docker Compose pins `n8nio/n8n` to `2.43.1` instead of `latest`.

### Cloud

- **Organizations and members.** Control plane (SQLAlchemy 2.0 async, Alembic `0001_initial`; SQLite by default, Postgres through `DAAS_CONTROL_DB_URL`) with organizations, users, memberships (`owner`, `admin`, `member`) and invitations. Each organization has its own data directory `runtime/tenants/<org_id>/` with its own DuckDB file, uploads, reports and audit lines.
- **Sign-in.** Magic link by e-mail (no passwords), cookie sessions (`HttpOnly`, `Secure`, `SameSite=Lax`) with CSRF double submit, `console` and `smtp` mail adapters. `DAAS_SECRET_KEY` (at least 32 characters) is required in cloud mode.
- **API keys.** Per organization, with scopes, expiry and revocation; shown once and stored as SHA-256.
- **API v1** under `/api/v1` with `application/problem+json` errors and `request_id`: organizations, members, invitations, API keys, uploads (CSV, XLSX, collector package), jobs, runs, reports (`ETag` = SHA-256), hash verification, artifact status of the latest collector run, `GET /me`, `/health`, `/ready`.
- **Jobs.** `POST .../jobs` answers `202`; in-process executor with one run per organization and `DAAS_MAX_CONCURRENT_RUNS` overall, state kept in the control database, recovery after restart (`running` becomes `failed` with `abandoned`), cancellation of queued jobs, progress over Server-Sent Events.
- **Cockpit.** `web/`: Next.js 16, React 19, TypeScript, Tailwind 4, no external CDN; Polish and English, light and dark; report in a sandboxed iframe; local SHA-256 check in the browser; strict CSP with nonce.
- **Webhooks.** `run.finished` with HMAC-SHA256 signature `X-DaaS-Signature`, three attempts with `DAAS_WEBHOOK_BACKOFF_SEC`, SSRF protection (public HTTPS addresses only).
- **Usage and plans.** `GET .../usage`; monthly limits of the `free` and `pro` plans answer `429 plan_limit`. No online payments.
- **Audit and metrics.** `GET .../audit` with cursor pagination; `GET /metrics` in Prometheus text format behind `DAAS_METRICS_TOKEN`.
- **Operator tools.** `python -m app.cli migrate | migrate-runtime | create-org`; `migrate-runtime` moves a 1.x `runtime/` into `runtime/tenants/default/` (idempotent).
- **Deploy.** `deploy/compose.yml` (`api`, `web`, optional Caddy with TLS), `Caddyfile`, `gen-env.sh`, `backup.sh` and `restore.sh` (UTC-dated tar with SHA-256 and retention), `smoke.sh`; `web/Dockerfile` (standalone, non-root); the API image now contains `alembic/` and `/data` volumes. Ports are published on `127.0.0.1` only. Update, rollback and shared-proxy instructions: `deploy/README.md`.
- **CI.** New jobs `web` (eslint, tsc, vitest, `next build`) and `e2e` (Playwright against the API in cloud mode with the console mail adapter); `pip-audit` in `security`; Python 3.13 in the test matrix; web image build and cloud-mode container smoke in `docker`.
- **Documentation.** `docs/API.md`, `docs/DEPLOY.md`, `docs/SECURITY.md`, `docs/DPA_v1_PL.md` (draft awaiting legal review), rewritten `docs/ARCHITECTURE.md`, pilot materials aligned with 2.0, READMEs for both modes.

### Known limitations

- Rate limits, the quota counter and pending webhook retries live in process memory (one worker, reset on restart).
- Docker was not run during this change; image builds, the container smoke tests and the Caddy configuration are covered by CI and by `deploy/smoke.sh` after installation.
- `selfhost` mode uses a single instance key and has no roles; roles exist in `cloud` mode.
- Cloud mode runs on one node with a SQLite control database; there is no failover. The `console` mail adapter is for trials only.
- No passkeys, SSO, online payments or external timestamps.

## [1.0.0] — 2026-09-28

Closure release. The engine is complete for its current scope and moves to maintenance mode:
the offer stays live and the code is frozen. `app/` and `tests/` change only for a paying client's
concrete requirement or the same request from three different clients (`AGENTS.md`, rule 8).

### Merged pull requests

- **#1** (2026-09-15) Hardened the client upload path against path traversal, removed the automatic push from the release script and added an OPSEC gate to CI.
- **#2** (2026-09-15) Wired the policy layer (workspace boundaries, role allowlist, audit log) into the runtime behind the `DAAS_POLICY_ENFORCE` flag.
- **#3** (2026-09-15) Secured `POST /upload`: streamed 10 MiB limit, filename and content validation, server-generated file names, atomic finalisation and audit events.
- **#4** (2026-09-16) Unified the audit log to a single configured path, with thread-safe append-only writes.
- **#5** (2026-09-16) Sped up CSV ingestion near the upload limit (about 1.5× on 9 MiB files) without changing the data contract.
- **#6** (2026-09-23) Added a DevSecOps baseline: ruff, gitleaks, semgrep, zizmor, SHA-pinned actions, Dependabot, pre-commit and a non-root container.
- **#8** (2026-09-23) Bumped the GitHub Actions dependency group (Dependabot).
- **#9** (2026-09-24) Added the `uksc_evidence` pipeline: a read-only PowerShell collector produces a hashed JSON package of Windows workstation controls, reported as control → UKSC/NIS2 article → status → evidence.
- **#10** (2026-09-23) Moved the Docker image to `python:3.12-slim`; Dependabot ignores Python ≥ 3.13.
- **#11** (2026-09-24) Rendered Polish diacritics in the client-facing UKSC report.

PR #7 (Python 3.14 base image) was closed without merging.

### Known limitations

- The Docker image is built and smoke-tested in CI on Linux; Docker has not been tested on Windows hosts.
- The n8n workflows in `n8n/` are JSON definitions only; no n8n instance is shipped or hosted.
- The runtime policy layer is off by default (`DAAS_POLICY_ENFORCE=false`).
- `POST /upload` accepts `.csv` only since #3 (smaller attack surface); `ecommerce_demo` still reads XLSX from a local path.
- Git history contains outdated author metadata; the history is intentionally not rewritten.

## [0.3] — 2026-09-07 … 2026-09-12 (before pull requests)

- Week-over-week metrics, Polish number format and actions derived from WoW deltas.
- XLSX upload test and n8n weekly workflow with revenue/margin drop alerts.
- Client-facing README (Polish and English), 50-second demo video, MIT license.

## [0.2] — 2026-09-07

- Initial engine: FastAPI + DuckDB + n8n, CSV/XLSX column mapper, native Windows runner without Docker.
