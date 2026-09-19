"""Transformacja UKSC Evidence: kontrole -> metryki per artykul ustawy + diff vs poprzedni run.

Zasady:
- coverage = udzial kontroli udokumentowanych automatycznie (PASS/FAIL/WARN) w calosci;
  MANUAL, NA_NO_ADMIN i ERROR NIE liczą sie jako "udokumentowane" - raport mowi to wprost.
- pass_pct liczone tylko w obrebie kontroli automatycznych.
- Nic tu nie ocenia "zgodnosci z ustawa" - to jest stan techniczny zmapowany na artykuly.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Any

import pandas as pd

from app.core.models import CheckStatus, UkscMetrics

AUTOMATED = {CheckStatus.PASS.value, CheckStatus.FAIL.value, CheckStatus.WARN.value}


def transform_uksc(records: list[dict[str, Any]], host: dict[str, Any], collected_at_utc: str | datetime,
                   previous: pd.DataFrame | None = None) -> UkscMetrics:
    statuses = [str(r.get("status")) for r in records]
    n = len(records)
    counts = {s.value: statuses.count(s.value) for s in CheckStatus}
    automated = sum(counts[s] for s in AUTOMATED)
    coverage = round(100.0 * automated / n, 1) if n else 0.0
    pass_pct = round(100.0 * counts["PASS"] / automated, 1) if automated else 0.0

    by_article: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for r in records:
        ref = str(r.get("uksc_ref") or "brak odniesienia")
        row = by_article.setdefault(ref, {"uksc_ref": ref, "controls": 0, "pass": 0, "fail": 0, "warn": 0,
                                          "manual": 0, "not_collected": 0})
        row["controls"] += 1
        st = str(r.get("status"))
        if st == "PASS":
            row["pass"] += 1
        elif st == "FAIL":
            row["fail"] += 1
        elif st == "WARN":
            row["warn"] += 1
        elif st == "MANUAL":
            row["manual"] += 1
        else:
            row["not_collected"] += 1
    for row in by_article.values():
        auto = row["pass"] + row["fail"] + row["warn"]
        row["documented"] = auto > 0
        row["verdict"] = ("FAIL" if row["fail"] else "WARN" if row["warn"] else "PASS") if auto else "MANUAL"

    def _brief(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "control_id": r.get("control_id"), "title": r.get("title"), "uksc_ref": r.get("uksc_ref"),
            "status": r.get("status"), "reference_threshold": r.get("reference_threshold"),
            "evidence": r.get("evidence") or {}, "exception_reason": r.get("exception_reason"),
        }

    failed = [_brief(r) for r in records if str(r.get("status")) in {"FAIL", "WARN"}]
    manual = [_brief(r) for r in records if str(r.get("status")) in {"MANUAL", "NA_NO_ADMIN", "ERROR"}]

    diff = None
    if previous is not None and len(previous):
        prev = {str(row["control_id"]): str(row["status"]) for _, row in previous.iterrows()}
        cur = {str(r.get("control_id")): str(r.get("status")) for r in records}
        improved = sorted(c for c in cur if prev.get(c) in {"FAIL", "WARN"} and cur[c] == "PASS")
        regressed = sorted(c for c in cur if prev.get(c) == "PASS" and cur[c] in {"FAIL", "WARN"})
        new = sorted(c for c in cur if c not in prev)
        gone = sorted(c for c in prev if c not in cur)
        diff = {"improved": improved, "regressed": regressed, "new_controls": new, "removed_controls": gone,
                "previous_checks": len(prev)}

    return UkscMetrics(
        host=str(host.get("name")),
        collected_at_utc=pd.to_datetime(collected_at_utc, utc=True).tz_localize(None).to_pydatetime(),
        is_admin_run=bool(host.get("is_admin_run")),
        checks_total=n,
        passed=counts["PASS"], failed=counts["FAIL"], warned=counts["WARN"], manual=counts["MANUAL"],
        na_no_admin=counts["NA_NO_ADMIN"], errors=counts["ERROR"],
        coverage_pct=coverage, pass_pct=pass_pct,
        by_article=list(by_article.values()),
        failed_controls=failed, manual_controls=manual,
        diff_vs_previous=diff,
    )
