"""Zrodlo UKSC Evidence: paczka JSON z collectora (tools/uksc-collector.ps1).

Priorytet:
1. source_path (plik z POST /upload, wylacznie w katalogu uploads/) -> LIVE_DATA
2. Fallback: data/fixtures/uksc/host_compliant.json -> MOCK_DATA

Kontrakt wejscia: app.core.models.UkscPackage (extra="forbid", schema_version sprawdzana).
Integralnosc: package_sha256 == sha256(konkatenacja posortowanych evidence_hash kontroli).
Zasada: zrodlo NIE zgaduje - blad walidacji = BLOCKED_MISSING_SECRET z opisem, nie czesciowy import.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings
from app.core.models import DataStatus, UkscPackage
from app.sources.cs2_source import SourceResult

log = logging.getLogger(__name__)

FIXTURE_FILE = "host_compliant.json"
MAX_PACKAGE_BYTES = 5 * 1024 * 1024  # 5 MiB - paczka ze stacji to kilkadziesiat KB; wiecej = cos nie tak


def canonical_evidence(evidence: dict[str, Any]) -> bytes:
    """Kanoniczny JSON dowodu: klucze posortowane ordinalnie, bez spacji, non-ASCII jako \\uXXXX.
    Identyczne bajty produkuje ConvertTo-CanonicalJson w tools/uksc-collector.ps1."""
    return json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def compute_evidence_hash(evidence: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_evidence(evidence)).hexdigest()


def compute_package_hash(evidence_hashes: list[str]) -> str:
    """Hash paczki = sha256 nad posortowana konkatenacja hashy dowodow (tylko hex, deterministyczne miedzy PS i Py)."""
    joined = "".join(sorted(h.lower() for h in evidence_hashes))
    return hashlib.sha256(joined.encode("ascii")).hexdigest()


class UkscSource:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.fixture_path: Path = settings.fixtures_path / "uksc" / FIXTURE_FILE

    # ------------------------------------------------------------------
    def fetch(self, force_mock: bool = False, source_path: str | None = None) -> SourceResult:
        if force_mock or not source_path:
            reason = "force_mock=true" if force_mock else "no source_path -> fixture fallback"
            return self._load(self.fixture_path, DataStatus.MOCK_DATA, reason)

        uploads_root = (self.settings.reports_path.parent / "uploads").resolve()
        p = Path(source_path).resolve()
        if not p.is_relative_to(uploads_root):
            return _blocked("source_path outside allowed uploads directory")
        if not p.exists():
            return _blocked("source_path not found")
        return self._load(p, DataStatus.LIVE_DATA, f"collector package: {p.name}")

    # ------------------------------------------------------------------
    def _load(self, path: Path, status: DataStatus, detail: str) -> SourceResult:
        if not path.exists():
            return _blocked(f"{detail}; package not found at {path}")
        if path.stat().st_size > MAX_PACKAGE_BYTES:
            return _blocked(f"package too large (> {MAX_PACKAGE_BYTES} bytes)")
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _blocked(f"package is not valid UTF-8 JSON: {exc}")
        try:
            pkg = UkscPackage.model_validate(raw)
        except ValidationError as exc:
            # pierwsze 3 bledy wystarcza do diagnozy; pelna lista poszlaby do logu
            errs = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:3])
            return _blocked(f"package failed schema validation: {errs}")

        # 0) kontrola z collectora BEZ hasha = paczka nieweryfikowalna. Usuniecie hashy nie moze
        #    omijac wykrywania edycji (review PR #9). Brak hasha dopuszczamy tylko dla zrodel innych
        #    niz collector (np. przyszle poswiadczenia reczne).
        missing = [c.control_id for c in pkg.checks if c.evidence_source == "powershell" and not c.evidence_hash]
        if missing:
            return _blocked(f"evidence_hash missing for collector checks: {', '.join(missing[:5])}")
        # 1) kazdy dowod: hash policzony z tresci musi zgadzac sie z zadeklarowanym (wykrywa edycje evidence)
        for c in pkg.checks:
            if c.evidence_hash and c.evidence_source == "powershell" and compute_evidence_hash(c.evidence) != c.evidence_hash:
                return _blocked(f"evidence_hash mismatch for {c.control_id} (evidence edited after collection)")
        # 2) paczka: hash nad posortowanymi hashami dowodow (wykrywa dodanie/usuniecie/podmiane kontroli)
        hashes = [c.evidence_hash for c in pkg.checks if c.evidence_hash]
        integrity = "unverifiable"
        if hashes and len(hashes) == len(pkg.checks):
            integrity = "ok" if compute_package_hash(hashes) == pkg.package_sha256 else "MISMATCH"
        if integrity == "MISMATCH":
            return _blocked("package_sha256 does not match evidence hashes (tampered or corrupted package)")

        records = [c.model_dump(mode="json") for c in pkg.checks]
        return SourceResult(
            records=records,
            data_status=status,
            detail=f"{detail} ({len(records)} checks, host {pkg.host.name}, integrity {integrity})",
            raw_meta={
                "path": str(path),
                "host": pkg.host.model_dump(),
                "collected_at_utc": pkg.collected_at_utc.isoformat(),
                "collector_version": pkg.collector_version,
                "schema_version": pkg.schema_version,
                "package_sha256": pkg.package_sha256,
                "integrity": integrity,
                "inventory": pkg.inventory,
            },
        )


def _blocked(detail: str) -> SourceResult:
    return SourceResult(records=[], data_status=DataStatus.BLOCKED_MISSING_SECRET, detail=detail)


def package_from_dict(raw: dict[str, Any]) -> UkscPackage:
    """Pomocnik dla testow i narzedzi: walidacja bez dotykania dysku."""
    return UkscPackage.model_validate(raw)
