# Changelog

All notable changes to DaaS Engine. Dates are merge dates (Europe/Warsaw).

## [2.0.0] — Unreleased

Hardening and cloud release. One code base, two modes (`DAAS_MODE=selfhost|cloud`). Behaviour changes in
the hardening sections are breaking for existing deployments: configure `DAAS_API_KEY` and update API
clients before upgrading. The `cloud` section is additive; `selfhost` keeps the existing routes.

### Changed

- Recovery reconciliation rejects pre-restore databases with no shared organization identifier when the restored database has organizations, including empty migrated databases, in both dry-run and apply without data or audit changes. Reconciliation is restricted operationally to the same host and the pre-restore archive from the current `restore.sh` invocation; new-host recovery and monthly drills use `--no-pre-backup` and the conservative reset procedure.
- `restore-reconcile --apply --resolve-owner <org_id>:<user_id>` resolves ownership conflicts explicitly after confirmation with the customer's owner; the option is repeatable. Only a pre-restore owner whose restored account and membership survive reconciliation may receive `owner`, atomically with the remaining changes and a ULID-only `restore.reconcile.owner_resolved` audit event. Invalid selections and unresolved conflicts leave the database unchanged. Reports identify conflicts and pre-restore owners with restored memberships without e-mails; a missing membership remains an isolated, platform-owner decision with no recovery command to resolve it.
- CI: OPSEC checks fail closed on scanner errors and scan the current tracked tree and PR/push range content (including merge resolutions and restorations, binary files except `*.png`/`*.mp4`, and UTF-16), file names, commit messages and author/committer identities using both policy files from the range base (bootstrap requires both files to be absent and the candidate Git blob IDs to match the owner-set `OPSEC_BOOTSTRAP_POLICY` repository variable), with commit-bound exceptions in `.opsec-history-allow`; merge additions are attributed only to non-merge origins reachable from a secondary parent that still contains the exact line or path; the workflow verifies a clean checkout of the tested SHA, records the policy blob IDs, hashes workflow names in warnings and runs on every PR `edited` event; any new exception requires an owner decision. Merge inheritance balances counts of exact line bytes within each path against all merge bases without tracking positions: relocating previously scanned identical bytes within a path produces no merge fingerprint, while additions outside that balance (extra copies, resurrected lines or paths, and new content) do; octopus merges receiving the same line from two sides and merge resolutions inside secondary branches can conservatively produce a merge fingerprint. Line bytes remain raw on every platform, including binary-mode awk and grep on Git for Windows.
- Migration `0006` revokes existing connector tokens without assigning legacy grants. Existing OAuth
  connections must be authorized again after upgrading.
- Invitations can grant only a role whose scopes the caller holds; an API key with only `members:manage`
  cannot invite anyone, including a `member`.
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

- Recovery maintenance mode (`DAAS_MAINTENANCE_MODE`, default `false`), exposed by `/api/v1/health`: queued work is not resumed, new runs, webhook tests, magic-link requests and invitation creation return `503 maintenance`, and scheduler, retention, webhook and notification activity is suspended.
- `python -m app.cli restore-quarantine [--dry-run] [--revoke-all]`: one transaction neutralizes restored sessions and one-time credentials, with count-only output and `restore.*` audit events. Conservative reset additionally revokes stored API/OAuth credentials, removes unaccepted invitations, disables webhook/reminder destinations, cancels queued jobs and disables run notifications and automatic runs on upload.
- `python -m app.cli restore-reconcile --pre <archive> [--apply]`: checksum-verified, read-only inspection of the pre-restore control database, dry-run by default, with restrictive changes and redacted count/ULID reports. Apply uses one database transaction with `restore.reconcile.*` audit events and deletes runtime files after commit; unresolved ownership conflicts prevent partial database changes. No objects are imported; explicit `--resolve-owner` conflict resolution is the only permitted role increase.
- `python -m app.cli restore-members`: offline membership review and removal/demotion for recovery without a usable pre-restore archive, with last-owner protection, no promotions and no e-mail output unless `--show-emails` is explicitly selected. Recovery documentation covers both paths, read-only archive mounting and owner verification before release; RUNBOOK recovery/update sections and the deployment rollback section are in Polish.
- OAuth isolation matrix for all MCP resources and prompts: foreign identifiers, own-resource controls,
  insufficient scopes, RFC 7009 and cockpit grant revocation, and immediate loss of access after membership removal.
- Retention audit actions `upload.purge_failed` and `report.purge_failed`, with counts per organization
  and cycle, and a `failed` file count in the purge summary (P1-5).
- Verification beyond SQLite and Chromium. The `postgres` CI job runs `tests/test_postgres.py` against the
  PostgreSQL 16 of the `ubuntu-24.04` runner: Alembic upgrade → downgrade → upgrade compared with the models, the
  "at least one owner" guard with two concurrent sessions (`SELECT … FOR UPDATE`; the test proceeds only when the
  server reports the second session as blocked, and a guard test shows that without the row lock both owners
  disappear) and the first-write race of the usage counter (SAVEPOINT, then `UPDATE`). `asyncpg` comes from
  `requirements-postgres.lock` (hashes, also audited by `pip-audit`); without `DAAS_TEST_POSTGRES_URL` the module is
  skipped. The `e2e-browsers` CI job runs the Playwright suite in Firefox (`desktop-firefox`) and WebKit
  (`mobile-webkit`, 375 px). WebKit refuses `Secure`/`__Host-` cookies over plain http, so its stack runs behind the
  e2e proxy with a throwaway self-signed certificate (`E2E_TLS=1`); production cookies are unchanged.
  `npm run e2e` keeps the two Chromium projects; `npm run e2e:firefox` and `npm run e2e:webkit` run the others.
- In-memory token-bucket rate limits for `/run`, `/upload`, per-IP traffic and failed keys (`429` with `Retry-After`), a 1 GiB upload quota (`413`) and cleanup of stale `*.tmp` files.
- XLSX support in `POST /upload` with container, macro, embedded-object, sheet and size limits; a workbook with several sheets needs the `sheet` field.
- Request id, JSON logging and security headers on every response; report responses add a restrictive `Content-Security-Policy`.
- A startup check that refuses more than one worker.
- `requirements.lock` and `requirements-dev.lock` with SHA-256 hashes; Docker and CI install with `--require-hashes`.
- Tests: an auth matrix over all routes, endpoint-level isolation, redaction, report output, limits, XLSX and consumers.
- Agent access over the Model Context Protocol: a built-in MCP server at `POST /mcp` (official Python SDK `mcp` 2.3,
  Streamable HTTP, stateless, JSON responses) with 12 tools (`whoami`, `list_pipelines`, `list_uploads`, `upload_csv`,
  `start_run`, `get_job`, `list_runs`, `get_run`, `get_report`, `verify_report`, `get_evidence`, `get_usage`). Same
  organization API keys and scopes as `/api/v1` (`Authorization: Bearer` or `X-API-Key`, no session cookie), same
  service functions, limits and audit (`mcp.tool` per call, without arguments); tenant isolation tested for every tool.
  Settings `DAAS_MCP_ENABLED`, `DAAS_MCP_MAX_BODY_BYTES` (3 MiB), `DAAS_MCP_MAX_UPLOAD_BYTES` (2 MiB),
  `DAAS_RATE_LIMIT_MCP_PER_MIN` (240 requests a minute per key); `/mcp` routed by the bundled Caddyfile. Not in
  `openapi.json`. Guide: `docs/MCP.md`. Upgrade note: the route is on by default in both modes (a valid API key is
  always required); set `DAAS_MCP_ENABLED=false` to remove it.
- `tools/lock_windows_pins.py` re-adds platform-specific lock entries (`uvloop`, `tzdata`, `pywin32`, `colorama`) with
  the hashes of every release file after `pip-compile`.
- **Schedule and notifications** ("the Monday report" without manual clicks, cloud): `GET`/`PUT /api/v1/orgs/{org_id}/schedule`
  (`org:read` / `org:manage`) with a weekly e-mail reminder to export (weekday, hour, IANA time zone, DST-safe; skipped
  after an upload in the last 6 days), automatic run of the suggested pipeline after an upload (`auto_job_id` in the
  upload answer; it needs the uploader's `runs:write`, the plan and per-key run limits apply and a file without
  recognised rows is not run — each of these keeps the upload and is audited as `schedule.auto_run_skipped`) and an
  e-mail to the members when a run finishes (metadata, SHA-256 and a link only; a partial run is named so; no report
  content, no token). Over MCP, `upload_csv` reports the auto-run job and `start_run` on an upload whose job is still
  queued or running returns that job (`reused: true`) instead of a second run. Reminders come from an in-process loop
  (`DAAS_SCHEDULER_ENABLED`, `DAAS_SCHEDULER_TICK_SEC`; one worker) that claims each slot before sending, so a reminder
  is never sent twice; a stored time zone that is no longer known disables that reminder with an audit entry instead
  of failing every tick. Migration `0004` adds `org_schedules`; the cockpit has a "Schedule and notifications" section
  in Settings (read only for members).
- **Data health dashboard** ("is next Monday's report safe?", cloud): `GET /api/v1/orgs/{org_id}/data-health`
  (`runs:read`; `weeks` 1-26, default 12) with uploads, finished jobs, reports and the mean run time per ISO week in
  the time zone of the schedule (Monday 00:00 local, DST-safe; the current week is partial), a summary (last upload,
  success and failure with its error code, streak of weeks with a report, schedule settings, plan usage) and issues
  with cockpit links, most severe first (`last_run_failed`, `plan_limit_reached`, `stale_upload`, `plan_limit_near`,
  `partial_reports`, `no_uploads_yet`, `reminder_off`, `auto_run_off`). Computed on the fly, read-only, not audited;
  new cockpit screen *Data health* (`/health`) with the verdict, key figures and an 8/12/26-week table with bars.
- **Chat notification after a run** (cloud): the schedule takes a Slack Incoming Webhook, Microsoft Teams (Workflows)
  or generic JSON address (`notify_webhook_kind`, `notify_webhook_url`; write-only secret — `GET` shows only
  `notify_webhook_set` and a mask such as `hooks.slack.com/…XyZ9`; an omitted address or kind stays unchanged, `null`
  removes the address). Each run then posts one message (status incl. partial, data period, report SHA-256, link to the run; no report
  content) with the SSRF rules and IP pinning of the signed webhooks, a single attempt with
  `DAAS_NOTIFY_WEBHOOK_TIMEOUT_SEC`, independent of the run e-mail, audited as `schedule.webhook_notified`; the
  cockpit section "Chat notification" sets it.
- **Report period and XLSX over MCP.** `POST .../jobs` and the MCP tool `start_run` take `period_from`/`period_to`
  (ISO dates, inclusive, either optional; at most 400 days, not before 2000-01-01, the same bounds as the self-hosted
  `POST /run`): an `ecommerce_demo` run counts only the orders dated in that range (filtered before storage and
  metrics; the source line names the period, the kept records and how many rows had no readable date; a period without orders ends `no_records` and uses no run of the plan); other pipelines answer
  `422 period_not_supported`. The job (`JobOut`, MCP `get_job`) keeps the requested period, `job.created` audits it and
  MCP reuses a pending job only for the same upload, pipeline and period. New MCP tool `upload_xlsx` (13 tools): an
  XLSX workbook as base64 through the same checks as the REST upload, `DAAS_MCP_MAX_UPLOAD_BYTES` counted on the
  decoded file, with explicit base64 padding validation on every supported Python version.
- **MCP resources and prompts:** four resource templates (`daas://runs/{run_id}/report.md`, `.../report.json`,
  `.../evidence.json`, `daas://uploads/{upload_id}/summary.json`) with the same content and SHA-256 as
  `get_report` / `get_evidence`, `resources/list` with the 20 newest reports of the key's organization, and three
  prompts (`weekly_summary`, `compare_runs`, `data_quality_check`) that tell the assistant which tools to call, to
  treat their output as data and to change nothing without the user's confirmation. Scopes and tenant isolation as
  for the tools (a foreign id is `not_found`); audit `mcp.resource` (template without the id) and `mcp.prompt`;
  errors are JSON-RPC errors with `code: message` that never echo the URI or argument values. Guide: `docs/MCP.md`.
- **OAuth 2.1 for MCP connectors** (cloud): ChatGPT and Claude connectors add `https://<host>/mcp` by URL, register
  themselves (RFC 7591) and get a token after the user approves them in the cockpit (`/oauth/consent`: organization,
  scopes narrowed to the role). Authorization server from the MCP SDK (`/authorize`, `/token`, `/register`, `/revoke`,
  RFC 8414 / RFC 9728 metadata, `resource_metadata` in the `401` of `/mcp`), PKCE S256 only, single-use 60-second
  codes (reuse revokes the tokens), rotating refresh tokens, tokens stored as SHA-256 and cut to the user's current
  role. `GET/DELETE /api/v1/orgs/{org_id}/oauth/grants` and Settings → Connected apps; audit `oauth.*`; settings
  `DAAS_OAUTH_*`; Caddy routes; the retention loop also drops registrations left a day without any code or token.
  Guide: `docs/MCP.md`.

### Security

- OAuth refresh and revocation share a database grant lock, so disconnecting also revokes a pair issued
  by a concurrent refresh (P1-2).
- Reusing a rotated refresh token revokes the entire grant and all its access and refresh tokens,
  including when the presented token has expired; rotation history is retained with the grant,
  while legacy tokens without a grant are purged after retention so unused clients can be removed (P1-3).
- Docker Compose pins `n8nio/n8n` to `2.45.5` (was `2.43.1`), fixing GHSA-9p7v-45m2-wxgj,
  GHSA-f2cw-9483-7vcp and GHSA-gx3v-3x3v-f8r5 (all rated High, published 2026-10-28).

### Fixed

- The SSE job-stream test waits for observed status events before changing job state, so a slow consumer cannot skip the initial queued status.
- Restore fails closed on pre-restore backup or service-stop failures, checks that API/web are stopped before replacing data, and validates restored SQLite integrity and foreign keys before any start. `--no-start` keeps services stopped; `--no-pre-backup` explicitly requires conservative quarantine. Backup names are unique and never overwrite an existing archive.
- Deployment smoke checks require explicit feature expectations or matching direct API responses before
  treating a proxy 404 for MCP or OAuth metadata as a disabled feature (P1-7).
- API v1 OpenAPI errors use the shared `Problem` schema and `application/problem+json`, including
  validation `errors[]`, matching the actual responses (P1-6).
- Cockpit CORS allows credentialed `PUT` preflights for schedule updates (P2-10).
- API key validation reports unknown scopes without echoing the submitted values (P2-11).
- Retention preserves upload records and the affected run's DuckDB rows, report paths and unpurged status
  when file removal or report path resolution fails, so later cycles can retry. Missing files still allow completion;
  successful purges are counted and audited only for uploads and runs whose files were removed or already absent (P1-5).
- Selfhost Markdown and JSON reports are served from the bytes used to compute `X-Report-SHA256`,
  even if the report file is replaced before the response is sent (P1-4).
- Selfhost OpenAPI describes the required `POST /run` JSON body and the existing redacted legacy
  validation response (`detail`, `code`, `request_id`) with status `422` (P2-8).
- OAuth `/token` and `/revoke` accept credentials supplied only through `client_secret_basic`, including
  form-encoded credentials, and reject conflicting client identities in the form (P2-9).

- The cockpit raised a Content-Security-Policy violation (`script-src 'eval'`) in Chromium, Firefox and WebKit
  after sign-in: zod 4 probes `new Function("")` before choosing its JIT. zod now runs `jitless`
  (`web/src/lib/zod-config.ts`), so the policy stays without `unsafe-eval` and the console stays clean. The e2e
  check collects `securitypolicyviolation` events in every engine; the old check read console text only and missed it.
- Client-supplied values are escaped before they reach HTML reports in all three report builders; the HTML renderer drops raw HTML, links and images.
- n8n workflows, the CI smoke test, the container health check and the PowerShell launcher send the API key.
- Docker Compose pins `n8nio/n8n` to `2.43.1` instead of `latest`.
- Windows hosts: the `console` mail outbox is owner-only through an NTFS DACL (`icacls`), not only the POSIX mode bits;
  files that cannot be restricted are not left on disk. After an XLSX parse timeout the request waits up to
  `XLSX_CANCEL_GRACE_SEC` for the worker to release the file, and temporary upload files that are still open are
  removed with retries instead of failing the response (sharing violations). The `console` outbox writes each
  message to a temporary name and publishes it with an atomic rename, so a reader never sees a half-written file
  (on Windows the DACL step runs between create and write). The test suite is green on Windows (Python 3.12) as well
  as Linux.
- Shutdown in `cloud` mode cancels the retention loop that the same lifespan started. With two lifespans on one
  application (two test clients) the second one replaced the shared reference, so the first never cancelled its own
  loop and, on Python 3.11, failed with `Event loop is closed` while waiting for a task from the other event loop.

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
- **Members and invitations.** `PATCH`/`DELETE .../members/{user_id}` (role change, removal; the last owner is protected with `409 last_owner`; removal ends the member's sessions and revokes the keys they created), `GET`/`DELETE .../invitations`; `POST .../invitations` always answers `202` and is limited to 20 per organization per 24 hours and 5 per minute per caller (`429 invitation_limit`).
- **Sign-in link bound to the browser.** The link only works without confirmation in the browser that requested it; elsewhere `verify` answers `409 confirm_required` (token not consumed) until the user confirms.
- **Retention.** `DAAS_RETENTION_INTERVAL_MIN` background task and `python -m app.cli purge [--dry-run]` delete uploads after `retention_until`, report files and DuckDB rows of runs older than `retention_days`; the run record and the report hash stay and the report endpoint answers `410 report_expired`. Migration `0003` adds the purge marker, the sign-in nonce and listing indexes (`0002` adds the data period of a run).
- **Event streams.** SSE limits `DAAS_SSE_MAX_PER_PRINCIPAL`, `DAAS_SSE_MAX_TOTAL`, `DAAS_SSE_MAX_SECONDS` (`429 sse_limit`), `503 shutting_down` for new streams during shutdown.
- **Operator tools.** `python -m app.cli migrate | migrate-runtime | purge | create-org`; `migrate-runtime` moves a 1.x `runtime/` into `runtime/tenants/default/` (idempotent).
- **Deploy.** `deploy/compose.yml` (`api`, `web`, optional Caddy with TLS), `Caddyfile`, `gen-env.sh`, `backup.sh` and `restore.sh` (UTC-dated tar with SHA-256 and retention), `smoke.sh`; `web/Dockerfile` (standalone, non-root); the API image now contains `alembic/` and `/data` volumes. Ports are published on `127.0.0.1` only. Update, rollback and shared-proxy instructions: `deploy/README.md`.
- **CI.** New jobs `web` (API schema drift, eslint, tsc, vitest, `next build`, `npm audit` of production dependencies), `e2e` (Playwright against the API in cloud mode with the console mail adapter) and `lighthouse` (mobile; `/login` and `/dpa`; performance >= 0.9, accessibility >= 0.95, best practices >= 0.9); `pip-audit` in `security`; Python 3.13 in the test matrix; web image build and cloud-mode container smoke (read-only file system, all capabilities dropped) in `docker`, plus validation of `compose.yml`, the LLM overlay and the Caddyfile; Dependabot for `npm` and `docker` in `web/`; the OPSEC grep also rejects the owner handle and the old brand string.
- **Deploy hardening.** `compose.yml` passes the `api` container an explicit list of variables instead of `env_file` (selfhost source keys and `ANTHROPIC_API_KEY` do not reach it; the LLM summary is switched on only by the `compose.llm.yml` overlay); `read_only` file systems with `tmpfs` for `api` and `web`; Caddy with `cap_drop: ALL` + `NET_BIND_SERVICE`; the Caddy log (access and error) drops `Referer` and masks the `?token=` of sign-in links; `DAAS_FORWARDED_ALLOW_IPS` for shared proxies; `gen-env.sh` sets `DAAS_ALLOW_CONSOLE_MAIL=true` next to the console adapter.
- **Documentation.** `docs/API.md`, `docs/DEPLOY.md`, `docs/SECURITY.md`, `docs/DPA_v1_PL.md` (draft awaiting legal review), rewritten `docs/ARCHITECTURE.md`, pilot materials aligned with 2.0, READMEs for both modes.
- **Pilot runbook.** `deploy/RUNBOOK.md` (Polish) for a single-VPS pilot: host preparation, DNS and mail (SPF, DKIM, DMARC), first deployment, encrypted off-server backups with a heartbeat, a monthly restore test on a separate machine with outbound container traffic blocked, monitoring, update and rollback, incident handling, and offboarding with data deletion. Placeholders only: provider choices, costs and pilot data stay outside the repository.

### Fixes after the security re-audit

- **XLSX.** Cells with formulas that have no stored value are no longer silently empty: the upload answer (`v1` and legacy `selfhost`) carries a warning in `warnings[]` asking to re-save the file in Excel or LibreOffice.
- **CSV numbers (ReDoS).** Number parsing is linear-time and a numeric cell longer than 64 characters is not parsed (unreadable value plus the usual warning).
- **Webhooks (SSRF).** The address check also covers NAT64 (`64:ff9b::/96`, judged by the embedded IPv4; `64:ff9b:1::/48` and 6to4 `2002::/16` refused) and reserved IPv6 ranges.
- **Members.** Removing or demoting the last owner is decided atomically in one statement (`409 last_owner`), also for concurrent requests.
- **Client address.** `deploy/Caddyfile` overwrites `X-Forwarded-For` explicitly (`header_up X-Forwarded-For {remote_host}`); verified on Caddy 2.10.2 that a forged header does not reach the API. `docs/SECURITY.md` explains when `FORWARDED_ALLOW_IPS=*` is safe.
- **API schema.** Unused `UsageOut`, `UsageRow`, `WebhookPing` and `WebhookPingResult` removed from the OpenAPI document (the ping answer is `WebhookPingOut`).
- **OPSEC.** Remaining owner name and statute references removed from the repository; the CI guard rejects more patterns (round 3 and `d0b1bb2`).
- **Gitleaks.** 8 synthetic test values in the history of `tests/` are excluded by their fingerprints in `.gitleaksignore` (owner decision of 2026-10-13; no path or regex allowlist, so any new finding still fails CI). The fingerprints depend on commit hashes: merge the PR with a merge commit, not squash or rebase. The `security` job now gets past gitleaks and runs semgrep, zizmor and pip-audit again.

### Fixes after the stranger test

An independent "outsider" test (13 agents installing and using the product from the documentation only, commit `61b55c2`) drove this wave. 12 of 13 paths passed with friction; the items below close the two product blockers and the documentation gaps that caused it.

- **Date parser (mixed formats in one column).** A single date column that mixes formats (`YYYY-MM-DD`, Excel native dates, `DD.MM.YYYY`) no longer silently swaps day and month where the day is ≤ 12 and understates the period report: dates are parsed per cell by their own format, and a column with mixed formats is flagged in the upload `warnings[]`. A single consistent format was already correct. Input guidance added to `docs/pilot/INPUT_REQUIREMENTS_PL.md` ("do not mix date formats in one column").
- **"Check yourself" hash in `selfhost` (REST).** The self-hosted legacy report path now exposes the report's SHA-256 (`/run` and `/reports/...`), so the "check yourself" promise holds outside the cockpit, MCP and cloud as well.
- **Report localization (PL).** With `locale=pl` the HTML report title and table headers are rendered in Polish (previously English regardless of locale).
- **Plan change path.** `python -m app.cli set-plan --org <id> --plan <free|pro|selfhost>` changes an organization's plan, and `PATCH /orgs` now rejects an unknown `plan` field instead of silently ignoring it; documented in `docs/API.md` and `docs/DEPLOY.md`.
- **Collector package upload (`uksc_evidence`), documented end to end.** `docs/API.md` and the pilot materials now carry a full worked-example (upload the `.json` package through the v1 route — in `selfhost` `org=default` with `DAAS_API_KEY`, because the legacy `POST /upload` takes CSV/XLSX only — then `uksc_evidence` job, `/evidence`, `verify`), state that `/verify` checks the report hash (not `package_sha256`), and document `diff_vs_previous` (computed per station/host).
- **Schedule `PUT` (docs).** `docs/API.md` now warns that `PUT /schedule` replaces the whole schedule (`GET` → edit → `PUT` the full object); a partial `PUT` silently resets `reminder_enabled`, `auto_run_on_upload` and `notify_on_finish`. Behaviour unchanged (the chat webhook pair stays the documented exception).
- **Secret rotation (docs).** `docs/SECURITY.md` now notes that after rotating `DAAS_SECRET_KEY` existing webhooks remain `active: true` but silently skip deliveries until deleted and re-created.
- **Documentation.** MCP tool count corrected to **13** in the `docs/MCP.md` intro; request-body examples for `POST /auth/magic-link`, `.../api-keys` and `.../invitations`; the minimal machine-key scope set (`uploads:write runs:write runs:read reports:read`, `runs:read` required to read the run back) in `docs/API.md` and the README; the first API key comes from `create-org` (`docs/MCP.md`, `docs/DEPLOY.md`); the n8n alert threshold (revenue drop ≥ 15% / margin ≥ 3 p.p.) and `n8n/pilot_workflow.json` added to the EN README, and a `selfhost` `curl` example aligned with the Polish README; `APP_HOST`/`APP_PORT` and the `requirements.lock` vs `-dev` note in both READMEs; the `runtime/tenants/default/` layout clarified in `.env.example`; `GET /reports/run/{run_id}` added to the legacy-routes list; `data-health` returns no `verdict` field; `retention_days` range (1–3650) and semantics in `docs/API.md`; webhook retry clarified (3 attempts = 2 pauses, the default `1,5,25` never uses `25`) and the `APP_ENV=dev` relaxation scoped to address/`https` only; `DAAS_NOTIFY_WEBHOOK_TIMEOUT_SEC` and `DAAS_RATE_LIMIT_MCP_PER_MIN` added to the `docs/DEPLOY.md` variable table.

### Known limitations

- Rate limits, the quota counter and pending webhook retries live in process memory (one worker, reset on restart).
- Docker was not run during this change; image builds and the container smoke tests (including the read-only file system) are covered by CI and by `deploy/smoke.sh` after installation. The Caddyfile was validated with `caddy validate` and its log filter exercised on a local Caddy.
- Retention does not reach backups or the technical audit trail. The LLM summary is not checked against the data and receives category and map names from the customer's file (prompt injection); see `docs/SECURITY.md`.
- `selfhost` mode uses a single instance key and has no roles; roles exist in `cloud` mode.
- Cloud mode runs on one node with a SQLite control database; there is no failover. The `console` mail adapter is for trials only: in `cloud` with `APP_ENV=prod` the server refuses to start with it unless `DAAS_ALLOW_CONSOLE_MAIL=true`.
- No passkeys, SSO, online payments or external timestamps.
- Deleting an organization removes its control-plane records and data directory but keeps the accounts of its users (e-mail address, optional name) in the control database; there is no command to delete a user yet. A failed sign-in e-mail shows only in the `api` log (`magic link e-mail failed`), because the endpoint answers `202` either way. SMTP uses STARTTLS (port 587); implicit TLS (port 465) is not supported.

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
- The runtime policy layer was off by default in 1.0.0 (`DAAS_POLICY_ENFORCE=false`); since 2.0.0 it is **on** by default.
- `POST /upload` accepts `.csv` only since #3 (smaller attack surface); `ecommerce_demo` still reads XLSX from a local path.
- Git history contains outdated author metadata; the history is intentionally not rewritten.

## [0.3] — 2026-09-07 … 2026-09-12 (before pull requests)

- Week-over-week metrics, Polish number format and actions derived from WoW deltas.
- XLSX upload test and n8n weekly workflow with revenue/margin drop alerts.
- Client-facing README (Polish and English), 50-second demo video, MIT license.

## [0.2] — 2026-09-07

- Initial engine: FastAPI + DuckDB + n8n, CSV/XLSX column mapper, native Windows runner without Docker.
