"""Rozwiazywanie sciezek plikow raportow zapisanych w bazie najemcy (wspolne dla API `runs` i retencji)."""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


def resolve_report(reports_dir: Path, raw_path: str | None, *, raise_errors: bool = False) -> Path | None:
    """Plik raportu w katalogu raportow najemcy albo None.

    Sciezka w bazie najemcy bywa bezwzgledna i po przeniesieniu katalogu runtime (inny host, inny punkt montowania)
    juz nie istnieje — wtedy szukamy pliku o tej samej nazwie w biezacym katalogu raportow. Zawsze bierzemy tylko
    nazwe pliku, wiec zadna sciezka z bazy nie wyprowadzi poza katalog raportow tej organizacji (po rozwiazaniu
    symlinkow wynik musi lezec w tym katalogu). Retencja uzywa `raise_errors`, aby blad rozwiazania sciezki
    nie byl uznany za brak pliku; pozostali wolajacy zachowuja wyszukiwanie best-effort.
    """
    if not raw_path:
        return None
    base = reports_dir.resolve()
    name = PureWindowsPath(raw_path).name or PurePosixPath(raw_path).name
    candidates = [Path(raw_path)]
    if name and name not in (".", ".."):
        candidates.append(base / name)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            if raise_errors:
                raise
            continue
        if resolved.is_relative_to(base) and resolved.is_file():
            return resolved
    return None
