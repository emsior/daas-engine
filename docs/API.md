# API v1

The cloud API lives under `/api/v1`. The machine-readable contract is [`openapi.json`](openapi.json) (generated from the application; regenerate with `python tools/export_openapi.py`). The interactive `/docs` and `/openapi.json` endpoints of a running server exist only with `APP_ENV=dev`.

AI assistants use the same keys and the same logic through the MCP server at `POST /mcp` (not part of `openapi.json`): see [`MCP.md`](MCP.md).

Examples below use these shell variables:

```bash
export DAAS=https://reports.example.com        # origin of the deployment (local: http://127.0.0.1:8000)
export KEY="daas_1a2b3c4d_..."                  # API key of one organization (shown once at creation)
export ORG="01J..."                             # organization id (GET /api/v1/me)
```

## Conventions

- JSON requests and responses (`Content-Type: application/json`), except file upload (`multipart/form-data`), report download and the SSE stream.
- Identifiers are ULIDs (organizations, keys, jobs, webhooks); uploads are 32 hex characters; runs are UUIDs.
- Timestamps are ISO 8601 in UTC.
- Every response carries `X-Request-ID`. Quote it when you report a problem; the same id is in the server log.
- Lists are cursor-paginated: `limit` and `cursor` query parameters, response `{ "items": [...], "next_cursor": "..." | null }`.

## Authentication

Two ways in; if both are sent, the key wins.

| Method | How | For |
|---|---|---|
| API key | header `X-API-Key: daas_<8 hex>_<secret>` | machines (n8n, scripts, CI) |
| Session cookie | `daas_session` (HttpOnly, Secure, SameSite=Lax) after the magic-link sign-in | the web cockpit |

Mutating requests made with a session cookie must repeat the value of the `daas_csrf` cookie in the `X-CSRF-Token` header (double submit). API-key requests do not use cookies and need no CSRF header.

There are no passwords. Sign-in is a single-use link sent by e-mail (`POST /auth/magic-link`, then `POST /auth/magic-link/verify` with the token from the link). The link is bound to the browser that asked for it (an HttpOnly nonce cookie set by `POST /auth/magic-link`). When it is opened in another browser, `verify` answers `409 confirm_required` with a masked `email_hint` and does **not** consume the token; repeat the call with `{"token": "...", "confirm": true}` after the user agrees to sign in as that address in this browser. Pending invitations for the address are accepted at sign-in. A key belongs to exactly one organization and carries scopes; a resource of another organization answers `404` (the members routes answer `403`).

```bash
# Request a sign-in link. "locale" (pl | en, default pl) picks the e-mail language. The answer is 202 for
# any address (it does not reveal whether an account exists).
curl -sS -X POST -H "Content-Type: application/json" \
  -d '{"email":"jan@example.test","locale":"pl"}' "$DAAS/api/v1/auth/magic-link"
```

### Scopes and roles

| Scope | Allows | member | admin | owner | assignable to a key |
|---|---|:-:|:-:|:-:|:-:|
| `org:read` | read organization, members | x | x | x | yes |
| `org:manage` | change name, retention, LLM opt-in | | x | x | yes |
| `members:manage` | add members, invite | | x | x | yes |
| `keys:manage` | list, create, revoke keys | | x | x | yes |
| `uploads:write` | upload files | x | x | x | yes |
| `runs:read` | list and read jobs and runs, SSE, data health | x | x | x | yes |
| `runs:write` | start and cancel jobs | x | x | x | yes |
| `reports:read` | download reports, verify hashes, artifact status | x | x | x | yes |
| `usage:read` | usage and plan limits | x | x | x | yes |
| `audit:read` | audit log | | x | x | yes |
| `webhooks:manage` | manage webhooks | | x | x | yes |
| `org:delete` | delete the organization | | | x | no (owner session only) |

A key can grant only scopes it holds itself (`403 scope_escalation` otherwise).

**Minimal scope set for a machine key** that uploads a file, starts a run and downloads the report (n8n, scripts, an MCP assistant): `uploads:write runs:write runs:read reports:read`. `runs:read` is required even for a key that only uploads and runs — without it the caller cannot poll the job or list the run to learn the `run_id`, so the report cannot be fetched. Add `usage:read` to let the key read plan limits and `webhooks:manage` to manage webhooks. Give a read-only analyst key only `runs:read reports:read usage:read`.

### Creating an API key and inviting members

```bash
# Create an API key (scope keys:manage). The full key appears once in the answer ("key"); afterwards only its
# metadata and prefix are returned. "scopes" must be a non-empty subset of the caller's own scopes.
# "expires_in_days" is optional (1-3650; omit or null = no expiry).
curl -sS -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"n8n production","scopes":["uploads:write","runs:write","runs:read","reports:read"],"expires_in_days":365}' \
  "$DAAS/api/v1/orgs/$ORG/api-keys"
# {"id":"01J...","prefix":"a1cfe4b6","key":"daas_a1cfe4b6_...","scopes":[...],"expires_at":"...","created_at":"..."}

# Invite a member by e-mail (scope members:manage). "role" is owner | admin | member (default member).
# Always answers 202 {"status":"accepted"} without revealing whether the address already has an account.
curl -sS -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"email":"ola@example.test","role":"member"}' "$DAAS/api/v1/orgs/$ORG/invitations"
```

## Errors

Errors are `application/problem+json` (RFC 9457) with a stable machine code:

```json
{
  "type": "urn:daas:problem:insufficient-scope",
  "title": "Forbidden",
  "status": 403,
  "detail": "insufficient permissions",
  "instance": "/api/v1/orgs/01J.../api-keys",
  "request_id": "b6f1c0f2a9d04e0f",
  "code": "insufficient_scope",
  "required_scope": "keys:manage"
}
```

Validation errors (`422`, `code: validation_error`) list `errors[]` with `loc`, `msg` and `type` only; the submitted value is never echoed.

| Status | `code` (examples) | Meaning |
|---|---|---|
| 400 | `bad_request` | malformed request |
| 401 | `unauthorized`, `invalid_token` | missing, wrong, expired or revoked key or session; sign-in link invalid, expired or used |
| 403 | `forbidden`, `insufficient_scope`, `scope_escalation`, `csrf_failed`, `user_session_required`, `org_protected`, `orgs_disabled_in_selfhost` | not allowed |
| 404 | `not_found` | no such resource in your organization |
| 409 | `conflict`, `confirm_required`, `last_owner`, `queue_full`, `not_cancellable`, `org_busy`, `key_limit_reached`, `webhook_exists`, `webhook_limit_reached` | state conflict (`confirm_required`: sign-in link opened in another browser; `last_owner`: the organization must keep one owner) |
| 410 | `report_expired` | the report files were removed by the retention job; the run record and its SHA-256 stay (`POST .../verify` still works) |
| 413 | `file_too_large`, `quota_exceeded` | file or upload quota too large |
| 415 | `extension_not_allowed` | file type not accepted |
| 422 | `validation_error`, `dpa_required`, `upload_required` (cloud: a pipeline that reads files needs `upload_id`, or `force_mock` for synthetic data), `mock_required`, `upload_kind_mismatch`, `upload_not_applicable`, `period_not_supported` (`period_from`/`period_to` for a pipeline other than `ecommerce_demo`), `events_required` (webhook without events), `webhook_url_invalid`, `invalid_cursor`, upload checks (`schema_mismatch`, `decode_failed`, `header_missing`, `too_many_records`, `xlsx_invalid`) | invalid content |
| 429 | `rate_limited`, `plan_limit`, `invitation_limit`, `sse_limit` | too many requests (header `Retry-After`), a monthly plan limit is used up, the invitation limit (20 per organization per 24 h, 5 per minute per caller) or the open event-stream limit is reached |
| 500 | `report_integrity_error` | the stored report no longer matches its recorded SHA-256; it is not served |
| 503 | `sessions_unavailable`, `webhooks_unavailable`, `shutting_down`, `unavailable` | the server lacks a required setting, is not ready, or is shutting down (`shutting_down` on a new event stream: retry after `Retry-After`) |

Rate limits are per client address and per key and are kept in process memory; respect `Retry-After`.

## Routes

`P` = public, `S` = needs a signed-in user session, `K` = scope for key or role (see above).

| Method | Path | Auth | Success | Errors | Purpose |
|---|---|---|---|---|---|
| GET | `/health` | P | 200 | | liveness (status, version, mode) |
| GET | `/ready` | P | 200 | 503 | readiness (control database, runtime directory) |
| GET | `/api/v1/health`, `/api/v1/ready` | P | 200 | 503 | same, under the version prefix |
| GET | `/metrics` | `Authorization: Bearer <DAAS_METRICS_TOKEN>` | 200 | 401, 404 | Prometheus text; 404 when no token is configured |
| POST | `/api/v1/auth/magic-link` | P | 202 | 422, 429, 503 | send a sign-in link (same answer for unknown addresses) |
| POST | `/api/v1/auth/magic-link/verify` | P | 200 | 401, 409, 422, 429 | exchange the token for a session (sets cookies); `409 confirm_required` when the link was requested in another browser |
| POST | `/api/v1/auth/logout` | S | 204 | 401, 403 | end the session |
| GET | `/api/v1/me` | key or session | 200 | 401 | caller, organizations, roles or key scopes |
| POST | `/api/v1/orgs` | S | 201 | 403, 422, 429 | create an organization; body must carry `dpa_accepted: true` |
| GET | `/api/v1/orgs/{org_id}` | `org:read` | 200 | 401, 404 | read |
| PATCH | `/api/v1/orgs/{org_id}` | `org:manage` | 200 | 403, 404, 422 | name, locale, `retention_days`, `llm_narrative` |
| DELETE | `/api/v1/orgs/{org_id}` | `org:delete` | 204 | 403, 409 | delete the organization and its data |
| GET | `/api/v1/orgs/{org_id}/members` | `org:read` | 200 | 403 | list members |
| PATCH | `/api/v1/orgs/{org_id}/members/{user_id}` | `members:manage`, owner session | 200 | 403, 404, 409 | change a role; the last owner cannot be demoted (`last_owner`) |
| DELETE | `/api/v1/orgs/{org_id}/members/{user_id}` | `members:manage` | 204 | 403, 404, 409 | remove a member (ends their sessions here and revokes the keys they created here); only an owner removes an owner; the last owner stays (`last_owner`) |
| POST | `/api/v1/orgs/{org_id}/invitations` | `members:manage` | 202 | 403, 429 | invite by e-mail (valid 7 days); always `{"status": "accepted"}`, no hint whether the address already has an account |
| GET | `/api/v1/orgs/{org_id}/invitations` | `members:manage` | 200 | 403 | pending invitations |
| DELETE | `/api/v1/orgs/{org_id}/invitations/{invitation_id}` | `members:manage` | 204 | 403, 404 | revoke a pending invitation |
| GET | `/api/v1/orgs/{org_id}/api-keys` | `keys:manage` | 200 | 403, 404 | list keys (metadata only) |
| POST | `/api/v1/orgs/{org_id}/api-keys` | `keys:manage` | 201 | 403, 409, 422 | create a key; the full key appears once |
| DELETE | `/api/v1/orgs/{org_id}/api-keys/{key_id}` | `keys:manage` | 204 | 403, 404 | revoke a key (immediate) |
| POST | `/api/v1/orgs/{org_id}/uploads` | `uploads:write` | 201 | 413, 415, 422, 429 | upload CSV, XLSX or a collector package (`.json`); `auto_job_id` when the schedule auto-runs it |
| POST | `/api/v1/orgs/{org_id}/jobs` | `runs:write` | 202 | 404, 409, 422, 429 | start a pipeline run (`Location` header); optional report period `period_from`/`period_to` |
| GET | `/api/v1/orgs/{org_id}/jobs` | `runs:read` | 200 | 404 | list jobs; filter `status` |
| GET | `/api/v1/orgs/{org_id}/jobs/{job_id}` | `runs:read` | 200 | 404 | job state and progress |
| POST | `/api/v1/orgs/{org_id}/jobs/{job_id}/cancel` | `runs:write` | 200 | 404, 409 | cancel a queued job |
| GET | `/api/v1/orgs/{org_id}/jobs/{job_id}/events` | `runs:read` | 200 (SSE) | 404, 429, 503 | live stream of one job (limits below) |
| GET | `/api/v1/orgs/{org_id}/runs` | `runs:read` | 200 | 404 | list runs |
| GET | `/api/v1/orgs/{org_id}/runs/{run_id}` | `runs:read` | 200 | 404 | run details and metrics |
| GET | `/api/v1/orgs/{org_id}/runs/{run_id}/report?fmt=html\|md\|json` | `reports:read` | 200, 304 | 404, 410, 500 | download a report; `410 report_expired` after retention |
| POST | `/api/v1/orgs/{org_id}/verify` | `reports:read` | 200 | 422 | does this SHA-256 belong to one of your reports |
| GET | `/api/v1/orgs/{org_id}/evidence` | `reports:read` | 200 | 404 | artifact status of the latest collector run |
| GET | `/api/v1/orgs/{org_id}/usage` | `usage:read` | 200 | 404 | monthly usage and plan limits |
| GET | `/api/v1/orgs/{org_id}/data-health?weeks=12` | `runs:read` | 200 | 404, 422 | weekly rhythm (uploads, reports, failures), summary and issues to fix |
| GET | `/api/v1/orgs/{org_id}/audit` | `audit:read` | 200 | 422 | audit log, newest first |
| GET | `/api/v1/orgs/{org_id}/schedule` | `org:read` | 200 | 404 | schedule and notifications (defaults when never saved; the chat webhook address only masked) |
| PUT | `/api/v1/orgs/{org_id}/schedule` | `org:manage` | 200 | 403, 404, 422 | save the schedule; recomputes `next_reminder_at`; an omitted `notify_webhook_url` stays unchanged |
| GET | `/api/v1/oauth/requests/{request_id}` | S | 200 | 403, 404, 410 | consent screen of an OAuth connector: application, requested scopes, return host, the user's organizations with the scopes each role can grant |
| POST | `/api/v1/oauth/requests/{request_id}/decision` | S | 200 | 403, 404, 409, 410, 422 | approve (`org_id`, optional `scopes`) or deny; answers `{redirect_to}` for the browser |
| GET | `/api/v1/orgs/{org_id}/oauth/grants` | `org:manage` | 200 | 403, 404 | connected applications (OAuth) |
| DELETE | `/api/v1/orgs/{org_id}/oauth/grants/{token_id}` | `org:manage` | 204 | 403, 404 | disconnect an application (revokes its tokens at once) |
| GET | `/api/v1/orgs/{org_id}/webhooks` | `webhooks:manage` | 200 | 403 | list webhooks |
| POST | `/api/v1/orgs/{org_id}/webhooks` | `webhooks:manage` | 201 | 409, 422, 503 | register an HTTPS endpoint; the signing secret appears once |
| DELETE | `/api/v1/orgs/{org_id}/webhooks/{webhook_id}` | `webhooks:manage` | 204 | 404 | delete |
| POST | `/api/v1/orgs/{org_id}/webhooks/{webhook_id}/ping` | `webhooks:manage` | 200 | 404 | send a test event; answers `{delivered, status_class}` |

Legacy routes exist only in `DAAS_MODE=selfhost` (organization `default`, key from `DAAS_API_KEY`, `Deprecation` response header). In `cloud` mode they answer `404`:

| Method | Path | Purpose |
|---|---|---|
| POST | `/upload` | upload a CSV or XLSX (only; collector packages go through the v1 route, see below) |
| POST | `/run` | run a pipeline synchronously (answer carries `report_path` and `report_html_path`) |
| GET | `/pipelines` | list pipelines |
| GET | `/runs`, `/runs/{run_id}`, `/runs/latest` | list and read runs |
| GET | `/reports/{pipeline}/latest?fmt=html\|md\|json` | the latest report of a pipeline |
| GET | `/reports/run/{run_id}?fmt=html\|md\|json` | the report of one run by id (the value `report_html_path` from `POST /run` points here) |
| GET | `/stats` | row counts |

For every format returned by the legacy `GET /reports/...` routes, `X-Report-SHA256` is the SHA-256 digest of the exact bytes sent in the response body.

**In `selfhost` the `/api/v1` routes also work**, for the single organization `org_id` = `default`, authenticated with `DAAS_API_KEY` (`X-API-Key` or `Authorization: Bearer`). Use them for anything the legacy routes do not cover — in particular uploading a collector package (`.json`), which the legacy `POST /upload` rejects (`415`, CSV and XLSX only); see [Status of collector artifacts](#status-of-collector-artifacts-uksc_evidence-end-to-end).

## Pipelines

| `pipeline` | Input | Upload kind |
|---|---|---|
| `ecommerce_demo` | order export: revenue, cost, margin, week-over-week | `csv` or `xlsx` |
| `uksc_evidence` | JSON package from the local collector (`tools/uksc-collector.ps1`): control, requirement, status, artifact hash | `package` |
| `cs2_demo` | esports statistics (public API or sample data) | none |

## From file to report

```bash
# 1. Upload a CSV (XLSX: same call with a .xlsx file; a workbook with several sheets needs -F sheet=Name)
curl -sS -H "X-API-Key: $KEY" -F "file=@data/fixtures/client_export_pl.csv" \
  "$DAAS/api/v1/orgs/$ORG/uploads"
# {"id":"3f2a...","kind":"csv","rows":15,"mapping":{"revenue":"Wartość brutto",...},"ready":true,
#  "suggested_pipeline":"ecommerce_demo",...}

# 2. Start the job: 202 Accepted + Location header
curl -sS -i -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"pipeline":"ecommerce_demo","upload_id":"3f2a...","client_name":"Demo Shop"}' \
  "$DAAS/api/v1/orgs/$ORG/jobs"
# {"id":"01J...","status":"queued","progress":0,...}

# 3. Follow it (poll, or use the SSE stream below)
curl -sS -H "X-API-Key: $KEY" "$DAAS/api/v1/orgs/$ORG/jobs/01J..."
# {"status":"succeeded","progress":100,"run_id":"c0a8...",...}

# 4. Download the report: html | md | json (ETag = SHA-256 of the file; If-None-Match accepts a list and weak W/"..." values)
curl -sS -H "X-API-Key: $KEY" "$DAAS/api/v1/orgs/$ORG/runs/c0a8.../report?fmt=html" -o report.html
```

The upload answer carries `warnings[]` (Polish text, not error codes) about what the mapper did with the data: numbers it could not read or read from an ambiguous thousand/decimal separator, rows with a negative revenue kept as corrections, rows skipped for an unreadable date or missing revenue, duplicate order ids (first one kept), a missing cost column, a numeric cell longer than 64 characters (not parsed; counted among the unreadable numbers), and for XLSX the cells with formulas that have no stored value ("N komórek z formułami bez zapisanych wartości — zapisz plik ponownie w Excelu/LibreOffice"; such cells are read as empty because formulas are never evaluated). The XLSX warning appears in the v1 upload answer and in the legacy `POST /upload` of `selfhost`. The same file always gives the same warnings; the upload is still `ready` when they appear.

Limits: CSV and XLSX up to 10 MiB (CSV at most 100 000 rows; XLSX without macros, at most 200 000 rows, 20 sheets and 256 columns); a collector package up to 5 MiB. A job is accepted for at most 20 queued or running jobs per organization; one run per organization executes at a time.

### Report for a date range

`POST .../jobs` accepts `period_from` and `period_to` (ISO `YYYY-MM-DD`, both inclusive; leave one out for an open range). An `ecommerce_demo` run then counts only the orders dated in that range, so a quarterly export gives the report for one week without cutting the file:

```bash
curl -sS -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"pipeline":"ecommerce_demo","upload_id":"3f2a...","period_from":"2026-08-01","period_to":"2026-08-07"}' \
  "$DAAS/api/v1/orgs/$ORG/jobs"
# {"id":"01J...","status":"queued","period_from":"2026-08-01","period_to":"2026-08-07",...}
```

- Validation (`422 validation_error`, `loc` names the field): `period_to` not before `period_from`, at most 400 days counting both ends, no date before 2000-01-01; a value that is not a `YYYY-MM-DD` date (`01.08.2026`, `2026-02-30`) is rejected too. Other pipelines (`uksc_evidence`, `cs2_demo`) answer `422 period_not_supported`.
- The orders are filtered before they are stored and before the metrics, so the report, the week-over-week figures and the stored rows describe the same records. The job (`JobOut`: answer, list, SSE snapshots) carries the requested `period_from`/`period_to`; `period_from`/`period_to` of the run (`GET .../runs/{run_id}`) stay the first and last order date actually in the report, and `source_detail` (also the source line of the report) says how many records were kept: `period 2026-08-01..2026-08-07 (8 of 15 records)`.
- A period without orders ends the job `failed` with `error_code: no_records`; such a run does not count towards the plan's run limit.
- The audit event `job.created` records `period_from`/`period_to` when they are set. A job queued automatically after an upload (schedule) has no period.

### Report integrity (verify)

The report is served with `ETag: "<sha256>"`. To check a copy you hold, compute its SHA-256 locally and ask whether the organization produced it. The answer never contains report content:

```bash
sha256sum report.html
curl -sS -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"sha256":"<64 hex characters>"}' "$DAAS/api/v1/orgs/$ORG/verify"
# {"known":true,"run_id":"c0a8...","pipeline":"ecommerce_demo","created_at":"...","format":"html"}
```

### Status of collector artifacts (`uksc_evidence`), end to end

The `uksc_evidence` pipeline turns a JSON package produced by the local collector (`tools/uksc-collector.ps1`) into a status report of technical artifacts. The package is uploaded through the **v1 upload route** (`kind=package`), not the legacy `POST /upload`, which accepts CSV and XLSX only (a `.json` answers `415`). This holds in `selfhost` too: use `/api/v1/orgs/default/uploads` with `DAAS_API_KEY` (the single organization is `default`).

```bash
# selfhost: ORG=default, KEY=$DAAS_API_KEY. cloud: ORG and KEY of the organization.

# 1. Upload the collector package (.json). The upload is detected as kind=package and suggests the pipeline.
#    For a package the CSV mapping fields are null (rows, mapping, ready); the package is still runnable.
curl -sS -H "X-API-Key: $KEY" -F "file=@uksc_WS-01_20261014.json" "$DAAS/api/v1/orgs/$ORG/uploads"
# {"id":"9b7e...","kind":"package","suggested_pipeline":"uksc_evidence","rows":null,"mapping":null,"ready":null,...}

# 2. Run uksc_evidence with the upload id (202 Accepted; this pipeline takes no period).
curl -sS -i -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"pipeline":"uksc_evidence","upload_id":"9b7e..."}' "$DAAS/api/v1/orgs/$ORG/jobs"

# 3. After the job has succeeded, read the artifact status of the latest collector run.
curl -sS -H "X-API-Key: $KEY" "$DAAS/api/v1/orgs/$ORG/evidence"
# {"run_id":"...","run_status":"SUCCESS","host":"WS-01","collected_at":"...","package_sha256":"...","integrity":"ok",
#  "checks":[{"control_id":"ENC-01","title":"...","requirement":"...","status":"PASS",
#             "artifact":{"source":"...","sha256":"..."}}]}
```

The response lists control, requirement, status and artifact hash (`EvidenceOut`: `run_id`, `run_status`, `host`, `collected_at`, `package_sha256`, `integrity`, `checks[]`). Artifact contents are not returned. `status` of a check is `PASS`, `FAIL`, `WARN`, `MANUAL`, `NA_NO_ADMIN` or `ERROR`. `run_status` is `SUCCESS` or `PARTIAL`; a `PARTIAL` run (incomplete package) is returned as the latest run, so check it before reading the checks as complete.

**Two different hashes — do not confuse them.** `POST .../verify` checks the SHA-256 of a **report file** (the ETag of `GET .../runs/{run_id}/report`), so it answers `known: true` for a report hash and `known: false` for a `package_sha256`. The `package_sha256` is the integrity hash of the uploaded package itself; it is printed in the report and is recomputed independently from the package (SHA-256 of the sorted concatenation of the per-artifact `evidence_hash` values), not through `/verify`.

**`diff_vs_previous` (change since the previous run of the same station).** The run report (the JSON report and section *"Zmiany względem poprzedniego runu tej stacji"* of the HTML) carries `diff_vs_previous`: `{"improved": [...], "regressed": [...], "new_controls": [...], "removed_controls": [...], "previous_checks": N}`, where `improved` is the controls that went `FAIL`/`WARN` → `PASS` and `regressed` the ones that went `PASS` → `FAIL`/`WARN`. It is computed **per host**: it compares against the previous run of the **same station** (`host`), so running two different stations one after another leaves `diff_vs_previous` `null` and the HTML section absent. To see a change, upload and run the same station twice.

## Live progress (SSE)

`GET /api/v1/orgs/{org_id}/jobs/{job_id}/events` answers `text/event-stream`:

| Event | Data |
|---|---|
| `status` | job snapshot, sent on every status change |
| `progress` | `{"id": "...", "progress": 0-100}` |
| `log` | `{"id": "...", "message": "...", "ts": "..."}` |
| `done` | final job snapshot; the stream then closes |

A comment line `: heartbeat` is sent after 15 seconds of silence (`DAAS_SSE_HEARTBEAT_SEC`) and the stream starts with `retry: 3000`.

```bash
curl -sN -H "X-API-Key: $KEY" "$DAAS/api/v1/orgs/$ORG/jobs/01J.../events"
```

Limits (`429 sse_limit` with `Retry-After`, per caller and in total; defaults in brackets): `DAAS_SSE_MAX_PER_PRINCIPAL` open streams per key or session (5), `DAAS_SSE_MAX_TOTAL` in the process (100), `DAAS_SSE_MAX_SECONDS` lifetime of one stream (1800). After the lifetime the server closes the stream: reconnect, and the first event is again a full `status` snapshot. On shutdown open streams end and a new one is refused with `503 shutting_down`. A reverse proxy must not buffer this response (the bundled Caddyfile sets `flush_interval -1`).

## Webhooks

```bash
curl -sS -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"url":"https://hooks.example.com/daas","events":["run.finished"]}' \
  "$DAAS/api/v1/orgs/$ORG/webhooks"
# {"id":"01J...","url":"https://hooks.example.com/daas","events":["run.finished"],"active":true,"secret":"..."}
```

The `secret` is shown once. Each delivery is `POST <url>` with a JSON body (sorted keys, no spaces) and these headers:

| Header | Value |
|---|---|
| `X-DaaS-Event` | `run.finished` (`ping` for the test call) |
| `X-DaaS-Delivery` | delivery id; identical across retries |
| `X-DaaS-Timestamp` | Unix seconds of this attempt |
| `X-DaaS-Signature` | `sha256=<hex HMAC-SHA256(secret, raw body)>` |

Body of `run.finished`: `event`, `org_id`, `job_id`, `run_id`, `pipeline`, `status` (`succeeded` or `failed`), `run_status` (`SUCCESS`, `PARTIAL`, `FAILED`), `finished_at`. It never contains report content or customer data. Fetch the report with the API key.

Verify the signature over the **raw** body before parsing:

```python
import hashlib, hmac

def valid(secret: str, raw_body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

Delivery rules: success is any `2xx` within 5 seconds; redirects are not followed; **at most 3 attempts in total** (one initial attempt plus up to 2 retries), with the pauses from `DAAS_WEBHOOK_BACKOFF_SEC`. 3 attempts mean only **2 pauses**, so with the default `1,5,25` only `1` (after the first attempt) and `5` (after the second) are used — the third value `25` is never reached. Pending retries are kept in memory and do not survive a restart. Use `X-DaaS-Delivery` to deduplicate. The signature covers the body only, not the timestamp header.

Registration rules:

- The URL must be `https`, carry no credentials and resolve only to public addresses (SSRF protection); a bad URL answers `422 webhook_url_invalid` with a `reason_code` (`https_required`, `private_address`, `credentials_in_url`, `url_malformed`). `APP_ENV=dev` relaxes **only the address range and the `https` requirement** (so an `http` loopback or a private address is accepted for local testing); `credentials_in_url` and `url_malformed` are rejected in every environment.
- At most 10 webhooks per organization (`409 webhook_limit_reached`); the same URL twice answers `409 webhook_exists`.
- The signing secret is derived from `DAAS_SECRET_KEY`; without that setting creation answers `503 webhooks_unavailable`.

Test call: `POST .../webhooks/{webhook_id}/ping` sends one signed `ping` event (single attempt, body `{"event":"ping","org_id":"..."}`) and answers `{"delivered": true, "status_class": "2xx"}`. `status_class` is `2xx`, `3xx`, `4xx`, `5xx` or `error` (no answer: network, DNS, address refused); the receiver's exact status code is not returned. The old `status` field is still present but always `null` (deprecated). Creating a webhook with an empty `events` list answers `422 events_required`.

## Schedule and notifications

"The Monday report" without manual clicks. One schedule per organization:

> **`PUT` replaces the whole schedule — send the full object.** `PUT` is not a partial update: every field you omit is reset to its default. A `PUT` that carries only, say, `notify_webhook_kind` silently turns `reminder_enabled`, `auto_run_on_upload` and `notify_on_finish` back to `false` (answer `200`, no error) — the "Monday report" stops running. Always `GET` the schedule first, change the fields you need in the returned object, and `PUT` the whole thing back. The only exception is the chat webhook pair `notify_webhook_url`/`notify_webhook_kind`: these two stay as saved when omitted (the address is write-only and cannot be read back to resend), so omit them unless you are changing the chat webhook.

```bash
curl -s -H "X-API-Key: $KEY" "$DAAS/api/v1/orgs/$ORG/schedule"
# {"reminder_enabled":false,"reminder_weekday":0,"reminder_hour":8,"timezone":"Europe/Warsaw",
#  "reminder_skip_if_recent_upload":true,"auto_run_on_upload":false,"notify_on_finish":false,
#  "notify_webhook_kind":"slack","notify_webhook_set":false,"notify_webhook_hint":null,
#  "next_reminder_at":null,"last_reminder_at":null,"updated_at":null}

curl -s -X PUT -H "X-API-Key: $KEY" -H "Content-Type: application/json" "$DAAS/api/v1/orgs/$ORG/schedule" \
  -d '{"reminder_enabled":true,"reminder_weekday":0,"reminder_hour":8,"timezone":"Europe/Warsaw",
       "reminder_skip_if_recent_upload":true,"auto_run_on_upload":true,"notify_on_finish":true,
       "notify_webhook_kind":"slack","notify_webhook_url":"https://hooks.slack.com/services/T000/B000/XXXX"}'
# {..., "notify_webhook_set":true, "notify_webhook_hint":"hooks.slack.com/…XXXX",
#  "next_reminder_at":"2026-10-19T06:00:00Z", "updated_at":"..."}
```

| Field | Default | Meaning |
|---|---|---|
| `reminder_enabled` | `false` | weekly e-mail "time for the export" to every member of the organization |
| `reminder_weekday` | `0` | 0 = Monday ... 6 = Sunday |
| `reminder_hour` | `8` | full hour 0-23, local time in `timezone` |
| `timezone` | `Europe/Warsaw` | IANA name; clock changes are handled (an hour that does not exist that day fires right after the jump, a repeated hour fires once) |
| `reminder_skip_if_recent_upload` | `true` | no reminder when the organization uploaded a file in the last 6 days (the date still moves one week) |
| `auto_run_on_upload` | `false` | after `POST .../uploads` a job for `suggested_pipeline` is queued the same way as `POST .../jobs` |
| `notify_on_finish` | `false` | e-mail to the members when a run finishes, success or failure |
| `notify_webhook_kind` | `slack` | format of the chat message after a run: `slack` (Slack Incoming Webhook), `teams` (Microsoft Teams Workflows, trigger "When a Teams webhook request is received") or `generic` (plain JSON for any receiver, e.g. n8n); omitted = unchanged (like the address) |
| `notify_webhook_url` | not set | write only (the address is a secret): omitted = unchanged, `null` = remove, text = set; at most 512 characters, `https`, a public address, no credentials (`http` and local addresses only with `APP_ENV=dev`); never returned |
| `notify_webhook_set`, `notify_webhook_hint` | `false`, `null` | read only: an address is saved, and its mask — host plus the last 4 characters of a long path, never the query (`hooks.slack.com/…XyZ9`) |
| `next_reminder_at`, `last_reminder_at` | `null` | read only, UTC; `next_reminder_at` is `null` while the reminder is off |
| `updated_at` | `null` | read only; `null` = defaults, never saved |

- `GET` needs `org:read` (every role), `PUT` needs `org:manage` (owner, admin). `PUT` replaces the whole schedule: an omitted field takes its default — except the chat webhook pair: `notify_webhook_url` stays as saved when omitted (the client cannot read it back to resend it) and so does `notify_webhook_kind` (a client that does not manage the chat webhook cannot switch a Teams address to the Slack format by accident). A weekday outside 0-6, an hour outside 0-23, an unknown zone or kind, or a chat webhook address that is too long, not `https`, points to a local or private address, or carries credentials answers `422 validation_error` with the field in `errors[].loc` (the value sent is not echoed; for the address `errors[].type` is `https_required`, `private_address`, `credentials_in_url`, `url_malformed` or `string_too_long`). Every save is audited as `schedule.updated` with the names of the changed fields only (never the address).
- Auto-run: the upload answer carries `auto_job_id` (the queued job; follow it like any job). The job is created on behalf of the uploader (`created_by`, audit actor) under the same conditions as `POST .../jobs`: the uploader needs `runs:write` (a key with `uploads:write` only never starts runs), the plan run limit and the per-key run rate limit apply. When any of them stops the run, when the queue is full, or when the file has no recognised rows (`ready: false`), the upload still succeeds (`201`), `auto_job_id` is `null` and the audit log gets `schedule.auto_run_skipped` with the code in `meta.reason` (`insufficient_scope`, `plan_limit`, `rate_limited`, `queue_full`, `upload_not_ready`, ...). Start the job by hand later. Over MCP, `start_run` on an upload whose auto-run job is still queued or running returns that job (`reused: true`) instead of a second one.
- Reminder e-mail: a fixed text and a link to the upload screen of the cockpit (no token; the member signs in as usual). Run e-mail: pipeline, status, data period, SHA-256 of the HTML report, error code on failure and a link to the run in the cockpit; never report content or customer data. Jobs that fail before the run starts (for example `SOURCE_UNAVAILABLE`) do not send it. Audit: `schedule.reminder` (`meta`: `sent`, `recipients`, `skipped_reason`), `schedule.notified`.
- Chat message (when `notify_webhook_url` is set; independent of `notify_on_finish` — both can be on, and a failure of one does not stop the other or the run): the same facts as the run e-mail (organization name without links, pipeline, status — a `PARTIAL` run is called partial —, data period, SHA-256 of the HTML report, error code on failure, finish time) and a button / link to the run in the cockpit; never report content or customer data. Texts are Polish. Shapes: Slack `{"text": "<one line>", "blocks": [section (mrkdwn facts), actions (button "Otwórz run")]}`; Teams `{"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": <Adaptive Card 1.4: TextBlock, FactSet, Action.OpenUrl>}]}`; `generic`:

  ```json
  {"event": "run.finished", "org_id": "01J...", "job_id": "01J...", "run_id": "7c9e...", "pipeline": "ecommerce_demo",
   "status": "succeeded", "run_status": "SUCCESS", "finished_at": "2026-10-19T06:03:12+00:00", "organization": "Acme",
   "period_from": "2026-10-12", "period_to": "2026-10-18", "report_sha256": "9f2c...", "error_code": null,
   "link": "https://reports.example.com/runs/7c9e..."}
  ```

  Delivery: one `POST` (`Content-Type: application/json`, UTF-8), no retries, timeout `DAAS_NOTIFY_WEBHOOK_TIMEOUT_SEC` (10 s), the same SSRF rules as the signed webhooks (public addresses only, the resolved IP is pinned, redirects are not followed); a `2xx` answer counts as sent. The `generic` body is **not signed**: the address itself is the secret — use the signed webhooks above when the receiver must verify the sender. Audit: `schedule.webhook_notified` (`meta`: `run_id`, `job_id`, `status` = run status `SUCCESS`/`PARTIAL`/`FAILED`, `channel` = kind, `sent`, `status_code` when the receiver answered, `error_type` on failure, e.g. `http_status`, `private_address`, `dns_failed`, `ReadTimeout`).
- Reminders are sent by a loop inside the API process (cloud mode only, see `docs/DEPLOY.md`); a reminder that fell due while the server was down goes out once after the start, the next one is computed from that moment.

## Connected applications (OAuth)

In `cloud` mode AI assistants (ChatGPT and Claude connectors) connect to `/mcp` through OAuth 2.1 instead of an API key; the flow and the endpoints of the authorization server (`/authorize`, `/token`, `/register`, `/revoke`, `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource/mcp`, outside `/api/v1` and outside `openapi.json`) are in [`MCP.md`](MCP.md#connect-from-chatgpt--claude-oauth). The API part is the consent screen of the cockpit and the list of connections:

```bash
# consent (user session + X-CSRF-Token); the id comes from /oauth/consent?request=<id>
curl -s -b cookies.txt "$DAAS/api/v1/oauth/requests/$REQUEST"
# {"request_id":"01J...","client_id":"6f1c...","client_name":"ChatGPT","scopes_requested":["runs:read","reports:read"],
#  "redirect_host":"chatgpt.com","expires_at":"...","organizations":[{"org_id":"01J...","name":"Acme","role":"owner",
#  "scopes_grantable":["runs:read","reports:read"]}]}
curl -s -b cookies.txt -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
  -d '{"approve":true,"org_id":"01J...","scopes":["runs:read"]}' "$DAAS/api/v1/oauth/requests/$REQUEST/decision"
# {"redirect_to":"https://chatgpt.com/...?code=...&state=..."}

curl -s -H "X-API-Key: $KEY" "$DAAS/api/v1/orgs/$ORG/oauth/grants"
# [{"token_id":"01J...","client_id":"6f1c...","client_name":"ChatGPT","user_id":"01J...","role":"owner",
#   "scopes":["runs:read"],"created_at":"...","last_used_at":"...","expires_at":"..."}]
```

- The consent routes accept only a signed-in user (an API key gets `403 user_session_required`); the decision needs the CSRF header. A request that is not pending answers `404`, an expired one `410 expired`, a second decision `409 request_already_decided`.
- Approve: the user must be a member of `org_id` (`403` otherwise, `422` when `org_id` is missing). Granted scopes = `scopes` (default: all) narrowed to the requested ones and to what the role allows; nothing left = `422 no_grantable_scopes`. `redirect_to` keeps the query of the registered redirect URI and adds `code` (single use, 60 seconds) and `state`; deny adds `error=access_denied`. Audit: `oauth.consent` (`client_id`, `scopes`).
- Grants: one item per connection (its active refresh token); `token_id` changes when the application refreshes. `user_id` and `role` name who approved it (`role: null` = no longer a member); no e-mail and no token value. `DELETE` revokes the refresh token and every access token issued from it, also when the id came from a list made before a refresh. Audit: `oauth.token_revoked` (`reason: disconnected`).

## Usage and plan limits

`GET /api/v1/orgs/{org_id}/usage` (scope `usage:read`):

```json
{
  "org_id": "01J...", "plan": "free", "retention_days": 90,
  "current":  {"period": "2026-10", "runs": 3, "upload_bytes": 1048576},
  "previous": {"period": "2026-09", "runs": 12, "upload_bytes": 52428800},
  "limits":   {"runs_per_month": 20, "upload_bytes_per_month": 209715200}
}
```

Periods are calendar months in UTC (`YYYY-MM`); a limit of `null` means unlimited.

`retention_days` (also in `data-health.summary`) is the organization's retention, set with `PATCH /api/v1/orgs/{org_id}` (scope `org:manage`); its range is **1–3650** (a value outside it answers `422 validation_error`, `loc: ["body", "retention_days"]`). It governs what the retention job deletes: uploads after their `retention_until` (fixed when the upload is stored) and the report files and DuckDB rows of runs older than `retention_days`. The run record and the report SHA-256 stay, so `POST .../verify` keeps working and `GET .../runs/{run_id}/report` answers `410 report_expired`. Backups are not touched. Defaults and the purge schedule: [`DEPLOY.md`](DEPLOY.md).

| Plan | Runs per month | Uploaded bytes per month |
|---|---|---|
| `free` | 20 | 200 MiB |
| `pro` | 500 | 5 GiB |
| `selfhost` | unlimited | unlimited |

When a limit is used up, `POST .../jobs` and `POST .../uploads` answer `429` with `code: plan_limit` and these extra fields:

```json
{"code": "plan_limit", "status": 429, "detail": "monthly runs limit of the free plan reached",
 "plan": "free", "resource": "runs", "limit": 20, "used": 20, "period": "2026-10"}
```

`resource` is `runs` or `upload_bytes`. Queued and running jobs count towards the run limit. The upload check uses the declared `Content-Length`, so it is approximate. A caller without access to the organization gets `404`/`403`, never `429`. There is no online payment in 2.0.

## Data health

`GET /api/v1/orgs/{org_id}/data-health?weeks=12` (scope `runs:read`, every role) answers one question: is next Monday's report safe, and if not, what to fix. It is computed on the fly from uploads, jobs, runs, the schedule and the plan usage; nothing is stored and reading it is not audited.

```bash
curl -s -H "X-API-Key: $KEY" "$DAAS/api/v1/orgs/$ORG/data-health?weeks=8"
# {"generated_at":"2026-10-14T10:00:00Z","timezone":"Europe/Warsaw",
#  "weeks":[{"week_start":"2026-08-24","partial":false,"uploads":1,"upload_bytes":48211,"runs_succeeded":1,
#            "runs_failed":0,"reports":1,"avg_run_seconds":4.2,"last_report_at":"2026-08-24T06:12:09Z"}, ...,
#           {"week_start":"2026-10-12","partial":true, ...}],
#  "summary":{"last_upload_at":"...","last_success_at":"...","last_failure_at":null,"last_error_code":null,
#             "streak_weeks":7,"reminder_enabled":true,"next_reminder_at":"2026-10-19T06:00:00Z",
#             "auto_run_on_upload":true,"notify_on_finish":true,"plan":"free","runs_used":9,"runs_limit":20,
#             "retention_days":90},
#  "issues":[]}
```

- **Weeks** are ISO weeks in the time zone of the schedule (`Europe/Warsaw` when the schedule was never saved or its zone is no longer known): a week starts on Monday 00:00 local time, so a file uploaded on a summer Sunday at 23:30 UTC (Monday 01:30 in Warsaw) counts for the new week, and the weeks with a clock change last 167 or 169 hours. The list runs from the oldest week to the current one, which is still running (`partial: true`). `weeks` is 1-26 (default 12); any other value answers `422 validation_error` with `loc: ["query", "weeks"]`.
- Per week: `uploads` and `upload_bytes` (by upload time); `runs_succeeded` and `runs_failed` (jobs, by the time they finished; `failed` includes `no_records`); `reports` = runs `SUCCESS` or `PARTIAL` whose job did not fail (a source without records is a failure, not a report); `avg_run_seconds` = mean `finished_at - started_at` of the finished jobs (`null` = none); `last_report_at`.
- `summary`: the last upload still on record (retention removes old upload records), the last job that finished `succeeded` and the last one that finished `failed` with its `error_code`, `streak_weeks` = finished weeks in a row with at least one report, counted back from last week (the current week neither extends nor breaks it; at most 25), the reminder, auto-run and run e-mail settings of the schedule, and the runs of the current calendar month (UTC) with the plan limit (`null` = unlimited).
- `weeks` only limits the list: the summary and the issues always look at the last 26 weeks, so switching between 8, 12 and 26 weeks in the cockpit never changes the verdict.

`issues` come most severe first (`error`, `warning`, `info`); `since` is when the condition started (`null` when unknown) and `link` is a path in the cockpit:

| `code` | `severity` | When | `link` |
|---|---|---|---|
| `last_run_failed` | `error` | the latest finished job failed (`since` = when it finished; the code is `summary.last_error_code`) | `/runs/{run_id}`, or `/upload` when the job has no run |
| `plan_limit_reached` | `error` | runs this month reached the plan limit (new jobs answer `429 plan_limit`) | `/settings` |
| `stale_upload` | `warning` | no upload for 7 days or more (`since` = the last upload; `null` when retention already removed its record) | `/upload` |
| `plan_limit_near` | `warning` | runs this month reached 80 % of the plan limit (16 of 20 on `free`) | `/settings` |
| `partial_reports` | `warning` | at least one `PARTIAL` report in the last 4 weeks, the current one included (`since` = the first of them) | `/` |
| `no_uploads_yet` | `info` | the organization never uploaded a file | `/upload` |
| `reminder_off` | `info` | the weekly export reminder is off | `/settings` |
| `auto_run_off` | `info` | a job does not start automatically after an upload | `/settings` |

The API response has **no `verdict` field**: it returns `issues` (and `summary`), and the caller derives the verdict from the highest `issues[].severity`. The cockpit screen *Data health* (`/health`) does exactly that: any `error` = at risk (red), only `warning` = at risk (yellow), no file yet = no data, otherwise safe (green).

## Audit log

`GET /api/v1/orgs/{org_id}/audit?limit=50&cursor=...` (scope `audit:read`; `limit` 1-200, default 50). Ordered by `(ts, id)` descending. The response is `{"items": [...], "next_cursor": "..." | null}`; the cursor is an opaque base64url value (an encoded `[ts, id]` pair): pass it back unchanged. A malformed cursor answers `422 invalid_cursor`. Events: `org.created`, `org.updated`, `org.deleted`, `member.added`, `member.invited`, `key.created`, `key.revoked`, `upload.created`, `job.created`, `job.cancelled`, `run.finished`, `webhook.created`, `webhook.deleted`, `webhook.delivered`, `webhook.failed`, `schedule.updated`, `schedule.reminder`, `schedule.auto_run_skipped`, `schedule.notified`, `schedule.webhook_notified`, `auth.login`, `auth.logout`, `auth_denied`, `mcp.tool` (one per MCP tool call: `tool`, `ok`, `duration_ms`, `key_id`; never the arguments). `meta` holds allow-listed technical fields only; file names, e-mail addresses and keys are never recorded.

## Metrics

`GET /metrics` (root path, not under `/api/v1`) returns Prometheus text format 0.0.4. It needs `Authorization: Bearer <DAAS_METRICS_TOKEN>`; a wrong or missing token answers `401`, and when no token is configured the route answers `404`. Expose it only on loopback (the bundled Caddyfile hides it). Labels never carry customer data.

| Metric | Type | Labels |
|---|---|---|
| `daas_build_info` | gauge | version, mode |
| `daas_http_requests_total` | counter | `route` (template), `status` |
| `daas_http_request_duration_seconds` | histogram | `route` (template), `le` |
| `daas_jobs` | gauge | status (`queued`, `running`, `succeeded`, `failed`, `cancelled`) |
| `daas_active_tenants` | gauge | none |

```bash
curl -sS -H "Authorization: Bearer $DAAS_METRICS_TOKEN" http://127.0.0.1:8000/metrics
```
