<!-- Process: docs/PROCESS.md. Rules for agents: AGENTS.md. Fill every section; write "n/a" with a reason if one does not apply. -->

## Summary

<!-- What changes and why, in two or three sentences. -->

## Author

<!-- Human or agent. For an agent: which one and the session or run that made the commits. -->

- Author:
- Session / run:

## Reviewer

<!-- Must be a different agent or person than the author. Verdict: ACCEPTED or CHANGES (with findings). -->

- Reviewer:
- Verdict:

## Evidence

<!-- Commands as run, with results as numbers and the date. Example: `python -m pytest -p no:cacheprovider` -> N passed, M skipped (YYYY-MM-DD). -->

- Commands and results (with date):
- CI run link:
- Head commit SHA:

## Scope

- In scope:
- Out of scope (seen but not changed):

## Checklist

- [ ] No secrets, tokens, keys, `.env` content or real customer data; test data is synthetic.
- [ ] No personal data and no local disk paths in files, commits or this description.
- [ ] Files staged by explicit path (no `git add .` / `git add -A`); no `--no-verify`.
- [ ] Tests run with the commands in `CLAUDE.md` (backend: pytest; `web/` changes: typecheck, lint, test) and the result is not below the baseline.
- [ ] Reviewer is not the author.
