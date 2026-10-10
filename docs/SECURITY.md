# Security

This document describes the threat model of DaaS Engine 2.0, what the code does about each threat, and what it does not. The customer-facing scope for pilots (in Polish) is [`pilot/SECURITY_SCOPE_PL.md`](pilot/SECURITY_SCOPE_PL.md).

## Reporting a vulnerability

Write to **prochpc@gmail.com** with the subject `DaaS Engine security`. Include the version (`GET /health`), what you did, what you expected and what happened; a `request_id` from the response helps. Do not open a public issue for a vulnerability and do not test against deployments you do not operate.

What to expect: an acknowledgement within 3 working days, an assessment and a fix plan within 14 days for confirmed issues, and a note in `CHANGELOG.md` when the fix ships. This is a small project: there is no bug bounty.

## What is protected

| Asset | Where it lives |
|---|---|
| Customer files (CSV, XLSX, collector packages) and generated reports | `runtime/tenants/<org_id>/` on the server |
| Account data (e-mail, organization, roles), API key hashes, audit trail | control database (`control.db`) |
| Secrets of the deployment (`DAAS_SECRET_KEY`, SMTP password, metrics token) | `deploy/.env`, mode 0600, never in the repository |

## Trust boundaries

```
 browser / API client ──TLS──► reverse proxy (Caddy) ──► web (Next.js) ──► api (FastAPI) ──► control.db
        untrusted                 limits body size,                          │               tenants/<org>/
                                  sets HSTS, hides /metrics                  └─ outbound only: SMTP, webhooks,
 collector on the customer's PC ─► uploads a JSON package (untrusted)           optional LLM provider
```

1. **Internet to proxy.** Everything from the network is untrusted. Only 80/443 are exposed; the application ports are bound to `127.0.0.1`.
2. **Tenant to tenant.** An organization must never see another's data. The organization comes from the credential, not from the request; every organization-scoped route resolves it before touching data and answers `404` for a foreign one.
3. **Customer content to the engine.** Uploaded files and collector packages are untrusted input, validated before they are stored or parsed. Values from them are untrusted text everywhere they are rendered.
4. **Engine to the outside.** Outbound calls are limited to e-mail, webhooks you register and, only when enabled, an LLM provider.

## Threats and controls

| Threat | Control |
|---|---|
| Stolen or guessed credentials | No passwords. Sign-in by a single-use link (15 minutes, 256-bit token, only its SHA-256 stored). API keys have 256 bits of entropy and are stored as SHA-256; compared in constant time; shown once; expire and can be revoked. Failed attempts are rate-limited per address. |
| Session theft, CSRF | Session cookie `HttpOnly; Secure; SameSite=Lax`, signed with `DAAS_SECRET_KEY`, server-side record with expiry. Mutations from a browser need the `X-CSRF-Token` header equal to the CSRF cookie and the value stored with the session (double submit). API keys use no cookies. |
| Privilege escalation | Roles `owner > admin > member` map to scopes; a key can grant only scopes it holds; `org:delete` exists only for an owner session; only an owner can grant `owner`. Denials are written to the audit trail. |
| Reading another organization's data (IDOR) | Organization-scoped dependency on every route; foreign ids answer `404`. Tests check that a second organization gets `404` for the first one's resources (`tests/test_orgs_v1.py`, `tests/test_jobs.py`, `tests/test_verify.py`); report files must stay inside the organization's report directory. |
| Malicious upload (path traversal, zip bomb, macros, CSV/formula tricks) | Server-generated file names (the client name is display-only), streamed size limit, per-organization byte quota, content and encoding checks, row limits. XLSX: container check, macros and embedded objects rejected, DTD rejected, limits on entries, unpacked size, compression ratio, sheets, rows and columns; formulas are never evaluated (cached values only). Temporary files are removed on failure and at start. |
| XSS through customer data in reports | Every value from CSV, XLSX and packages is escaped before the HTML report is built; the Markdown renderer drops raw HTML, links and images. Report responses carry `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; img-src data:`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. The cockpit shows a report only inside `<iframe sandbox>` without `allow-scripts` and `allow-same-origin`. |
| XSS in the cockpit | Strict CSP with a per-request nonce and `strict-dynamic`, no inline styles, no `unsafe-inline`, no external CDN or fonts, `frame-ancestors 'none'`; ESLint forbids `dangerouslySetInnerHTML` and inline `style`. |
| SSRF through webhooks | Outside `APP_ENV=dev` the URL must be `https`, contain no credentials and resolve only to public addresses; names are resolved again at delivery time; redirects are not followed; 5-second timeout; at most 10 webhooks per organization. |
| Forged webhook deliveries | Every delivery is signed `X-DaaS-Signature: sha256=HMAC-SHA256(secret, body)`; the payload contains identifiers and statuses, never report content. Consumers must verify the signature and deduplicate on `X-DaaS-Delivery`. |
| Abuse and denial of service | In-process token-bucket limits (per key, per address, failed keys, sign-in requests per address and per e-mail), request size limits at the proxy (12 MiB for `/api/*`), upload quota, at most 20 queued or running jobs per organization, one run per organization at a time, global run limit, monthly plan limits (`429 plan_limit`). |
| Information leaks | Public `/health` and `/ready` expose only status, name, version and mode. API errors are `{code, detail, request_id}` without paths or exception text; the detail is in the log under the same `request_id`. `/docs`, `/redoc` and `/openapi.json` exist only with `APP_ENV=dev`. Access logs omit headers and query strings; the proxy log drops key, cookie and authorization headers. Audit `meta` is allow-listed. |
| Data sent to third parties | The LLM summary is off by default; it needs the organization's opt-in and a provider key. Only computed metrics are sent (no order ids, product names, customer or host names, no package hashes) and the report labels the summary as model-generated. |
| Compromised dependency or image | Python dependencies are pinned with SHA-256 hashes and installed with `--require-hashes`; CI runs `pip-audit`, gitleaks (full history), semgrep and zizmor; GitHub Actions are pinned to commit SHAs; the `api` and `web` images run as non-root users, and their containers start with `no-new-privileges` and all capabilities dropped. |
| Loss or leak of backups | Backups are tar archives with a SHA-256 file, mode 0600. They contain customer data and are not encrypted by the scripts: encrypt and store them off the server. |

## Data handling

- Uploads are deleted after the retention period set per organization (default 90 days). Deleting an organization removes its control-plane records and its data directory.
- The audit trail records who did what and when (identifiers, technical metadata, address); it does not record file names, file content, e-mail addresses or key values.
- The data processing agreement (version 1, a draft awaiting legal review) is [`DPA_v1_PL.md`](DPA_v1_PL.md).

## Known limitations

These are accepted for 2.0 and documented so that nobody relies on more than is there:

- Rate limits, the quota counter and pending webhook retries live in process memory: one worker only (the server refuses to start with more), counters reset on restart, and a restart drops retries in flight.
- Webhook deliveries run in the application process and are not persisted: a restart loses retries still pending (only finished deliveries leave an audit entry).
- Rotating `DAAS_SECRET_KEY` invalidates every webhook signing secret (secrets are derived from it) as well as all sessions; webhooks must be re-created to get new secrets.
- Webhook addresses are checked when registered and resolved again at every delivery, but there is no pinning of the resolved address: a DNS rebinding race between the check and the connection is not excluded.
- The webhook signature covers the body only. A captured delivery can be replayed; receivers should deduplicate on `X-DaaS-Delivery`.
- The upload byte quota (storage and monthly plan limit) is enforced while streaming, but the monthly plan check before the request uses the declared `Content-Length` and is therefore approximate.
- One node. The control database is SQLite in a volume; there is no failover. Jobs running during a restart end as `failed` with code `abandoned`.
- The `console` mail adapter writes sign-in links to the log and to `runtime/outbox/`. It is for trials and end-to-end tests only; production needs `DAAS_EMAIL_BACKEND=smtp`.
- Behind a proxy the application trusts `X-Forwarded-For` from the compose network (`FORWARDED_ALLOW_IPS=*`). That is safe only while the API port is not reachable by untrusted hosts; do not publish it on a public interface.
- No passkeys, SSO or two-factor sign-in (planned for a later release). A mailbox compromise is an account compromise.
- Three roles only; no per-resource permissions.
- Reports are integrity-checked by SHA-256 against the control database. There is no external timestamp (RFC 3161) and no signature by a trusted third party, so the hash proves "this file is what the engine produced", not when.
- The collector reads local Windows settings and hashes them; the engine cannot tell whether the collector itself was tampered with. Treat the package as the customer's statement.
- The legacy `selfhost` mode has a single instance-wide key and no roles.
- Docker image builds and the Caddy configuration were validated for syntax only in the build environment (no container runtime); run `deploy/smoke.sh` after the first installation.
- The data processing agreement is a draft and needs legal review before production use.

## Hardening checklist for operators

1. `deploy/.env` readable only by the operator; the secret kept in a secret manager as well.
2. `DAAS_EMAIL_BACKEND=smtp`; SPF and DKIM set for the sender domain.
3. Firewall: 22, 80, 443 only. Never publish ports 8000 or 3000 on a public interface.
4. Automatic backups (`deploy/backup.sh`) copied off the server and encrypted; a restore tested on another machine.
5. Update on every release (`CHANGELOG.md`); subscribe to the repository's Dependabot pull requests.
6. Watch `docker compose logs api` for `auth_denied` and `429`, and `/metrics` (token required, loopback only).
