"""Redakcja odpowiedzi API: bez sciezek serwera i bez tresci wyjatkow.

Wewnetrzny `RunResult` (runner, DuckDB, raporty) zachowuje sciezki i pelny blad — dzieki temu
log i debugowanie sa kompletne. Na krawedzi API zamieniamy je na linki i kod bledu.
"""
from __future__ import annotations

import re

from app.core.models import RunResult

#: Typ wyjatku (prefiks `error` z runnera) -> kod publiczny. Reszta = RUN_FAILED.
ERROR_CODES: dict[str, str] = {
    "FileNotFoundError": "SOURCE_UNAVAILABLE",
    "PolicyError": "POLICY_DENIED",
    "ValidationError": "INVALID_DATA",
    "ValueError": "INVALID_DATA",
}

#: Sciezka absolutna (POSIX albo Windows) w tekscie. Nie rusza "n/a", "120/120" ani adresow URL.
_ABS_PATH = re.compile(r"(?<![\w/.:])(?:[A-Za-z]:[\\/]|/)[^\s;|,)\"']{2,}")


def scrub_paths(text: str | None) -> str | None:
    """Zastepuje sciezki absolutne znacznikiem `<path>`."""
    if text is None:
        return None
    return _ABS_PATH.sub("<path>", text)


def error_code_of(raw: str | None) -> str | None:
    """Kod publiczny z surowego `error` runnera (`TypWyjatku: tresc`); None gdy nie bylo bledu."""
    if not raw:
        return None
    return ERROR_CODES.get(raw.split(":", 1)[0].strip(), "RUN_FAILED")


def public_error(raw: str | None, run_id: str) -> str | None:
    """`TypWyjatku: tresc` -> `KOD: komunikat ogolny (run_id=...)`. Szczegoly sa w logu serwera."""
    if not raw:
        return None
    exc_type = raw.split(":", 1)[0].strip()
    code = ERROR_CODES.get(exc_type, "RUN_FAILED")
    return f"{code}: przebieg nie powiodl sie; szczegoly w logu serwera (run_id={run_id})"


def redact_run(result: RunResult) -> RunResult:
    """Kopia `RunResult` bezpieczna do zwrocenia klientowi: linki zamiast sciezek, ogolny blad."""
    rid = result.run_id

    def link(path: str | None, fmt: str) -> str | None:
        return f"/reports/run/{rid}?fmt={fmt}" if path else None

    return result.model_copy(update={
        "report_path": link(result.report_path, "md"),
        "report_json_path": link(result.report_json_path, "json"),
        "report_html_path": link(result.report_html_path, "html"),
        "error": public_error(result.error, rid),
        "source_detail": scrub_paths(result.source_detail),
    })
