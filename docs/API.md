# API v1

The cloud API lives under `/api/v1`. The machine-readable contract is [`openapi.json`](openapi.json) (generated from the application; regenerate with `python tools/export_openapi.py`). The interactive `/docs` and `/openapi.json` endpoints of a running server exist only with `APP_ENV=dev`.

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

### Scopes and roles

| Scope | Allows | member | admin | owner | assignable to a key |
|---|---|:-:|:-:|:-:|:-:|
| `org:read` | read organization, members | x | x | x | yes |
| `org:manage` | change name, retention, LLM opt-in | | x | x | yes |
| `members:manage` | add members, invite | | x | x | yes |
| `keys:manage` | list, create, revoke keys | | x | x | yes |
| `uploads:write` | upload files | x | x | x | yes |
| `runs:read` | list and read jobs and runs, SSE | x | x | x | yes |
| `runs:write` | start and cancel jobs | x | x | x | yes |
| `reports:read` | download reports, verify hashes, artifact status | x | x | x | yes |
| `usage:read` | usage and plan limits | x | x | x | yes |
| `audit:read` | audit log | | x | x | yes |
| `webhooks:manage` | manage webhooks | | x | x | yes |
| `org:delete` | delete the organization | | | x | no (owner session only) |

A key can grant only scopes it holds itself (`403 scope_escalation` otherwise).

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
| 422 | `validation_error`, `dpa_required`, `upload_required` (cloud: a pipeline that reads files needs `upload_id`, or `force_mock` for synthetic data), `mock_required`, `upload_kind_mismatch`, `upload_not_applicable`, `events_required` (webhook without events), `webhook_url_invalid`, `invalid_cursor`, upload checks (`schema_mismatch`, `decode_failed`, `header_missing`, `too_many_records`, `xlsx_invalid`) | invalid content |
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
| POST | `/api/v1/orgs/{org_id}/uploads` | `uploads:write` | 201 | 413, 415, 422, 429 | upload CSV, XLSX or a collector package (`.json`) |
| POST | `/api/v1/orgs/{org_id}/jobs` | `runs:write` | 202 | 404, 409, 422, 429 | start a pipeline run (`Location` header) |
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
| GET | `/api/v1/orgs/{org_id}/audit` | `audit:read` | 200 | 422 | audit log, newest first |
| GET | `/api/v1/orgs/{org_id}/webhooks` | `webhooks:manage` | 200 | 403 | list webhooks |
| POST | `/api/v1/orgs/{org_id}/webhooks` | `webhooks:manage` | 201 | 409, 422, 503 | register an HTTPS endpoint; the signing secret appears once |
| DELETE | `/api/v1/orgs/{org_id}/webhooks/{webhook_id}` | `webhooks:manage` | 204 | 404 | delete |
| POST | `/api/v1/orgs/{org_id}/webhooks/{webhook_id}/ping` | `webhooks:manage` | 200 | 404 | send a test event; answers `{delivered, status_class}` |

Legacy routes (`/upload`, `/run`, `/pipelines`, `/runs`, `/reports/...`, `/stats`) exist only in `DAAS_MODE=selfhost` (organization `default`, key from `DAAS_API_KEY`, `Deprecation` response header). In `cloud` mode they answer `404`.

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

### Report integrity (verify)

The report is served with `ETag: "<sha256>"`. To check a copy you hold, compute its SHA-256 locally and ask whether the organization produced it. The answer never contains report content:

```bash
sha256sum report.html
curl -sS -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"sha256":"<64 hex characters>"}' "$DAAS/api/v1/orgs/$ORG/verify"
# {"known":true,"run_id":"c0a8...","pipeline":"ecommerce_demo","created_at":"...","format":"html"}
```

### Status of collector artifacts

```bash
# Upload the package from the local collector, run uksc_evidence, then:
curl -sS -H "X-API-Key: $KEY" "$DAAS/api/v1/orgs/$ORG/evidence"
# {"run_id":"...","run_status":"SUCCESS","host":"...","package_sha256":"...","checks":[
#   {"control_id":"...","requirement":"...","status":"...","artifact":{"source":"...","sha256":"..."}}]}
```

The response lists control, requirement, status and artifact hash. Artifact contents are not returned. `run_status` is `SUCCESS` or `PARTIAL`; a `PARTIAL` run (incomplete package) is returned as the latest run, so check it before reading the checks as complete.

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

Delivery rules: success is any `2xx` within 5 seconds; redirects are not followed; at most 3 attempts in total, with the pauses from `DAAS_WEBHOOK_BACKOFF_SEC` (default `1,5,25`); pending retries are kept in memory and do not survive a restart. Use `X-DaaS-Delivery` to deduplicate. The signature covers the body only, not the timestamp header.

Registration rules:

- The URL must be `https`, carry no credentials and resolve only to public addresses (SSRF protection); outside `APP_ENV=dev` anything else answers `422 webhook_url_invalid` with a `reason_code` (`https_required`, `private_address`, `credentials_in_url`, `url_malformed`).
- At most 10 webhooks per organization (`409 webhook_limit_reached`); the same URL twice answers `409 webhook_exists`.
- The signing secret is derived from `DAAS_SECRET_KEY`; without that setting creation answers `503 webhooks_unavailable`.

Test call: `POST .../webhooks/{webhook_id}/ping` sends one signed `ping` event (single attempt, body `{"event":"ping","org_id":"..."}`) and answers `{"delivered": true, "status_class": "2xx"}`. `status_class` is `2xx`, `3xx`, `4xx`, `5xx` or `error` (no answer: network, DNS, address refused); the receiver's exact status code is not returned. The old `status` field is still present but always `null` (deprecated). Creating a webhook with an empty `events` list answers `422 events_required`.

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

## Audit log

`GET /api/v1/orgs/{org_id}/audit?limit=50&cursor=...` (scope `audit:read`; `limit` 1-200, default 50). Ordered by `(ts, id)` descending. The response is `{"items": [...], "next_cursor": "..." | null}`; the cursor is an opaque base64url value (an encoded `[ts, id]` pair): pass it back unchanged. A malformed cursor answers `422 invalid_cursor`. Events: `org.created`, `org.updated`, `org.deleted`, `member.added`, `member.invited`, `key.created`, `key.revoked`, `upload.created`, `job.created`, `job.cancelled`, `run.finished`, `webhook.created`, `webhook.deleted`, `webhook.delivered`, `webhook.failed`, `auth.login`, `auth.logout`, `auth_denied`. `meta` holds allow-listed technical fields only; file names, e-mail addresses and keys are never recorded.

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
