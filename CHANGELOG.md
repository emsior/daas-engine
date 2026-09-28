# Changelog

All notable changes to DaaS Engine. Dates are merge dates (Europe/Warsaw).

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
