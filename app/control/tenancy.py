"""Data plane: katalog najemcy i rejestr runnerow per organizacja.

Uklad na dysku (jeden najemca = jeden katalog, zadnych wspolnych plikow miedzy najemcami):

    runtime/tenants/<org_id>/daas.duckdb   baza DuckDB najemcy (jeden writer na plik)
    runtime/tenants/<org_id>/uploads/      pliki klienta (<upload_id>.csv | .json)
    runtime/tenants/<org_id>/reports/      raporty html/md/json
    runtime/tenants/<org_id>/audit.jsonl   audyt najemcy (ta sama nazwa pliku co w trybie legacy)

`TenantRegistry` otwiera runnery leniwie i trzyma co najwyzej `DAAS_MAX_ACTIVE_TENANTS` naraz (LRU; najemca
z trwajacym runem nie jest zamykany). Zapisy do DuckDB jednego najemcy serializuje `asyncio.Lock` z `lock()`.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from app.core.config import AUDIT_LOG_FILENAME, ORG_ID_PATTERN, Settings
from app.pipelines.runner import PipelineRunner

log = logging.getLogger(__name__)


class InvalidOrgId(ValueError):
    """Identyfikator organizacji niezdatny do uzycia w sciezce (separatory, kropki, zbyt dlugi)."""


@dataclass(frozen=True)
class TenantPaths:
    """Sciezki najemcy. Konstruktor `for_org` waliduje id i pilnuje, ze katalog lezy w `tenants/`."""

    org_id: str
    root: Path

    @classmethod
    def for_org(cls, settings: Settings, org_id: str) -> TenantPaths:
        if not isinstance(org_id, str) or not ORG_ID_PATTERN.match(org_id):
            raise InvalidOrgId("invalid organization id")
        base = settings.tenants_root.resolve()
        root = (base / org_id).resolve()
        if root == base or not root.is_relative_to(base):
            raise InvalidOrgId("organization directory escapes the tenants root")
        return cls(org_id=org_id, root=root)

    @property
    def duckdb(self) -> Path:
        return self.root / "daas.duckdb"

    @property
    def uploads(self) -> Path:
        return self.root / "uploads"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def audit_log(self) -> Path:
        return self.root / AUDIT_LOG_FILENAME

    def ensure(self) -> TenantPaths:
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)
        return self

    def contains(self, candidate: Path) -> bool:
        """True, gdy `candidate` (po rozwiazaniu symlinkow) lezy w katalogu uploadow najemcy."""
        return candidate.resolve().is_relative_to(self.uploads.resolve())


def tenant_settings(settings: Settings, paths: TenantPaths, *, llm_narrative: bool | None = None) -> Settings:
    """Kopia `Settings` skierowana na katalog najemcy. Wszystko, co liczy sciezki z `duckdb_file`,
    `reports_path` i `uploads_path` (zrodla pipeline'ow, strażnik uploadu, audyt), dziala wtedy na danych najemcy."""
    update: dict[str, object] = {"duckdb_path": str(paths.duckdb), "reports_dir": str(paths.reports)}
    if llm_narrative is not None:
        update["daas_llm_narrative"] = llm_narrative
    return settings.model_copy(update=update)


@dataclass
class _Entry:
    runner: PipelineRunner
    llm_narrative: bool | None


class TenantRegistry:
    """Leniwy rejestr runnerow z limitem aktywnych najemcow (LRU) i zamkiem zapisu per organizacja."""

    def __init__(self, settings: Settings, max_active: int | None = None) -> None:
        self.settings = settings
        self.max_active = max_active or settings.daas_max_active_tenants
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._in_use: dict[str, int] = {}
        self._guard = threading.RLock()
        self.evictions = 0

    # ------------------------------------------------------------------ runnery
    def paths(self, org_id: str) -> TenantPaths:
        return TenantPaths.for_org(self.settings, org_id)

    def get(self, org_id: str, *, llm_narrative: bool | None = None) -> PipelineRunner:
        """Runner najemcy (tworzy katalog i baze przy pierwszym uzyciu). Blokujace — z kodu async uzyj `aget`.

        `llm_narrative=None` znaczy "dowolny": istniejacy runner jest zwracany bez przebudowy (odczyty API nie
        moga wyrzucac runnera ustawionego przez executor). Jawne True/False przebudowuje runner, gdy sie rozni.
        """
        paths = self.paths(org_id).ensure()
        with self._guard:
            entry = self._entries.get(org_id)
            if entry is not None and (llm_narrative is None or entry.llm_narrative == llm_narrative):
                self._entries.move_to_end(org_id)
                return entry.runner
            runner = PipelineRunner(settings=tenant_settings(self.settings, paths, llm_narrative=llm_narrative))
            self._entries[org_id] = _Entry(runner, llm_narrative)
            self._entries.move_to_end(org_id)
            self._evict_over_limit(keep=org_id)
            return runner

    async def aget(self, org_id: str, *, llm_narrative: bool | None = None) -> PipelineRunner:
        return await asyncio.to_thread(self.get, org_id, llm_narrative=llm_narrative)

    def _evict_over_limit(self, keep: str) -> None:
        for org_id in list(self._entries):
            if len(self._entries) <= self.max_active:
                return
            if org_id == keep or self._in_use.get(org_id, 0) > 0:
                continue
            del self._entries[org_id]
            self.evictions += 1
            log.info("tenant runner evicted (LRU) org_id=%s", org_id)

    def evict(self, org_id: str) -> None:
        with self._guard:
            if self._in_use.get(org_id, 0) == 0:
                self._entries.pop(org_id, None)

    def active(self) -> list[str]:
        with self._guard:
            return list(self._entries)

    def mark_in_use(self, org_id: str) -> None:
        with self._guard:
            self._in_use[org_id] = self._in_use.get(org_id, 0) + 1

    def release(self, org_id: str) -> None:
        with self._guard:
            self._in_use[org_id] = max(0, self._in_use.get(org_id, 0) - 1)

    def close_all(self) -> None:
        with self._guard:
            self._entries.clear()

    # ------------------------------------------------------------------ zapis
    def lock(self, org_id: str) -> asyncio.Lock:
        """Zamek zapisu DuckDB tego najemcy (jeden proces, jeden writer na plik)."""
        lock = self._locks.get(org_id)
        if lock is None:
            lock = self._locks[org_id] = asyncio.Lock()
        return lock

    def forget(self, org_id: str) -> None:
        """Po usunieciu organizacji: zapomina runner i zamek."""
        with self._guard:
            self._entries.pop(org_id, None)
            self._locks.pop(org_id, None)
            self._in_use.pop(org_id, None)
