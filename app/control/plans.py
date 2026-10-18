"""Plany i ich limity miesieczne. Jedno zrodlo prawdy dla `GET /usage` i egzekwowania przy `POST /jobs|/uploads`.

`None` = bez limitu (plan `selfhost`). Limity dotycza miesiaca kalendarzowego UTC (`UsageCounter.period`).
"""
from __future__ import annotations

from dataclasses import dataclass

MIB = 1024 * 1024
GIB = 1024 * MIB


@dataclass(frozen=True)
class PlanLimits:
    runs_per_month: int | None
    upload_bytes_per_month: int | None


PLAN_LIMITS: dict[str, PlanLimits] = {
    "free": PlanLimits(runs_per_month=20, upload_bytes_per_month=200 * MIB),
    "pro": PlanLimits(runs_per_month=500, upload_bytes_per_month=5 * GIB),
    "selfhost": PlanLimits(runs_per_month=None, upload_bytes_per_month=None),
}

UNLIMITED = PlanLimits(runs_per_month=None, upload_bytes_per_month=None)


def limits_for(plan: str) -> PlanLimits:
    """Limity planu; nieznany plan traktujemy jak `free` (bezpieczniej niz brak limitu)."""
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])


def previous_period(period: str) -> str:
    """`2026-01` -> `2025-12`."""
    year, month = int(period[:4]), int(period[5:7])
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"
