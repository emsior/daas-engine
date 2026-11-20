# How this project is built

This repository is built by AI coding agents working for one human owner. This page says who does what,
what evidence a change needs, and which parts are enforced by tooling and which only by practice.

Last checked: 2026-11-16.

## Roles

| Role | Who | What it does |
|---|---|---|
| Executor | an AI agent (Claude Code or Codex CLI; the owner assigns the roles and has swapped them over time) | writes the change on a working branch from a task spec |
| Reviewer | a different AI agent than the author | reads the diff and the evidence, answers ACCEPTED or CHANGES with findings |
| Owner | the human who owns the repository | sets the tasks, decides on merge and on pushes that touch `main` |

The rule is **author ≠ reviewer**: Claude reviews changes written by Codex, and Codex reviews changes written by Claude
([`AGENTS.md`](../AGENTS.md), section "Zasady PPC dla Codexa").

## What a change has to show

[`AGENTS.md`](../AGENTS.md) asks every task to name its source, the allowed operation, what is out of bounds and the
evidence. Before handing a change off, the agent runs the test commands from [`CLAUDE.md`](../CLAUDE.md) and reports
the result as a number with a date; a result below the recorded baseline counts as a regression. The result block
lists the evidence (commands and results with date and time) and the state change (files and commits). The pull
request template ([`.github/pull_request_template.md`](../.github/pull_request_template.md)) asks for the same:
commands with results and date, a CI link and the commit SHA.

## Branches, pull requests and `main`

- Changes reach `main` only through a working branch and a pull request ([`AGENTS.md`](../AGENTS.md), section "Git").
- Files are staged by explicit path, never `git add .` or `git add -A`; hooks are not skipped (`--no-verify`) and
  published history is not rewritten (same section).
- Push to `main`, force push and deleting remote branches need one-time consent from the owner (same section).
  The owner reports running a pre-push hook on their own machine that asks for that consent; it is not part of
  this repository.
- `main` is a protected branch. GitHub settings, read via the API on 2026-11-16: three status checks are required
  before merge (`test (3.11)`, `test (3.12)`, `opsec`), force push and deletion are disabled, approving reviews
  required: 0, rules not enforced for admins.

## Automated checks

- **CI** ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) runs on every pull request and every push to
  `main`: `test` (Python 3.11, 3.12, 3.13, with ruff), `postgres`, `security` (gitleaks, semgrep, zizmor,
  pip-audit), `web`, `e2e`, `e2e-browsers`, `lighthouse` and `docker`. Only the three checks listed above block a
  merge; the other jobs report.
- **OPSEC** (`.github/workflows/opsec.yml`) runs on pull requests and pushes to `main`. It runs the repository's own
  scanner over the checked-out tree and over the lines added in the pushed or proposed commit range, against the
  project's vocabulary and privacy policy for public files. It takes the scanner and its allowlist from the base
  commit when the base has them, so a pull request cannot approve its own exception.
- **pre-commit** ([`.pre-commit-config.yaml`](../.pre-commit-config.yaml)) with gitleaks, ruff and a few file checks
  is optional: each clone installs it, or runs it by hand. CI runs gitleaks and ruff on its own either way.

## Traces in the history

About three quarters of the commits (172 of 233 on `main` at commit f6f8e26, counted on 2026-11-16) carry a `Claude-Session:`
footer that points to the Claude Code session that made them. No comparable footer marks commits from the other
agent, so the footer shows where Claude worked, not who wrote everything else.

Closed pull requests: <https://github.com/emsior/daas-engine/pulls?q=is%3Apr+is%3Aclosed>. In PR #13 the owner
account posted [a table](https://github.com/emsior/daas-engine/pull/13#issuecomment-6220057476) of which agent wrote and which agent reviewed each commit range, with verdicts. It is a
self-reported record; the full review notes are kept outside the repository.

## What the repository does not enforce

- The roles and the author ≠ reviewer rule are written instructions for agents in [`AGENTS.md`](../AGENTS.md).
  No GitHub setting requires a review (0 required approvals), so review happens by practice, not by a gate.
- The evidence format and the result block are conventions; no check validates them.
- pre-commit runs only where someone has installed or started it.
- The owner's consent step for pushes runs on the owner's machine, outside this repository.
