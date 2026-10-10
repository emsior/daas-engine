"""Formatowanie liczb w raportach (konwencja PL) i sanityzacja wartosci nieskonczonych.

Jedno zrodlo prawdy dla generatora, narracji i transformacji: `7 255,95` (zwykla spacja jako separator tysiecy,
przecinek dziesietny), brak "-0,00", brak `nan`/`inf` w tekscie i w JSON.
"""
from __future__ import annotations

import math
from typing import Any


def pln(x: float | int | None) -> str:
    """Format PL: `7 255,95`. `None` -> `—`; NaN/inf oraz wartosci zaokraglajace sie do zera -> `0,00`."""
    if x is None:
        return "—"
    value = float(x)
    if not math.isfinite(value):
        return "0,00"
    value = round(value, 2)
    if value == 0:                      # takze -0.0 i -0.004
        value = 0.0
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def sanitize_floats(node: Any) -> Any:
    """Rekurencyjnie zamienia NaN/inf na `None` (JSON nie ma tych wartosci; `allow_nan=False` rzuci wyjatek)."""
    if isinstance(node, float):
        return node if math.isfinite(node) else None
    if isinstance(node, dict):
        return {k: sanitize_floats(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [sanitize_floats(v) for v in node]
    return node
