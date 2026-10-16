"""`python -m app.cli migrate-runtime`: runtime/ z wersji 1.x -> runtime/tenants/default/ (idempotentne).

Przenosi plik bazy DuckDB (z `.wal`), katalogi `reports/` i `uploads/` oraz log audytu. Potem:
    * w bazie przeniesionej organizacji przepisuje bezwzgledne sciezki raportow (`runs.report_*_path`),
    * uzupelnia w control plane organizacje `default` i `RunRecord` (z hashami istniejacych raportow),
      zeby historia sprzed migracji byla widoczna w API v1.

Nic nie jest kasowane ani nadpisywane: gdy cel juz istnieje, element zostaje na miejscu i trafia do
raportu jako konflikt (kod wyjscia 1). Drugi przebieg nie robi nic.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.control import service
from app.control.db import create_engine, make_sessionmaker, upgrade_sync
from app.control.models import RunRecord
from app.control.tenancy import TenantPaths
from app.core.config import AUDIT_LOG_FILENAME, Settings
from app.storage.duckdb_client import DuckDBClient

log = logging.getLogger(__name__)

#: Stary i nowy (przejsciowy) wariant nazwy logu audytu.
LEGACY_AUDIT_NAMES = (AUDIT_LOG_FILENAME, "audit.log")


@dataclass
class MigrationReport:
    dry_run: bool = False
    moved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    rewritten_paths: int = 0
    backfilled_runs: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.moved or self.rewritten_paths or self.backfilled_runs)

    def lines(self) -> list[str]:
        prefix = "would move" if self.dry_run else "moved"
        out = [f"{prefix}: {item}" for item in self.moved]
        out += [f"skipped (nothing to do): {item}" for item in self.skipped]
        out += [f"CONFLICT (target exists, left in place): {item}" for item in self.conflicts]
        out.append(f"report paths rewritten: {self.rewritten_paths}")
        out.append(f"run records backfilled: {self.backfilled_runs}")
        out.append("nothing changed" if not self.changed and not self.conflicts else "done")
        return out


def _move_file(src: Path, dst: Path, label: str, report: MigrationReport, dry_run: bool) -> None:
    if not src.exists():
        report.skipped.append(label)
        return
    if dst.exists():
        report.conflicts.append(label)
        return
    report.moved.append(label)
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


def _move_dir_children(src: Path, dst: Path, label: str, report: MigrationReport, dry_run: bool) -> None:
    if not src.is_dir() or src.resolve() == dst.resolve():
        report.skipped.append(label)
        return
    children = [c for c in sorted(src.iterdir()) if c.name != ".gitkeep"]
    if not children:
        report.skipped.append(label)
        return
    for child in children:
        _move_file(child, dst / child.name, f"{label}/{child.name}", report, dry_run)
    if not dry_run:
        try:
            for keep in src.glob(".gitkeep"):
                keep.unlink()
            src.rmdir()  # tylko pusty katalog; niepusty (konflikt) zostaje
        except OSError:
            pass


def _rewrite_report_paths(db: DuckDBClient, old_reports: Path, new_reports: Path) -> int:
    """Zamienia prefiks katalogu raportow w `runs.report_*_path`. Zwraca liczbe zmienionych pol."""
    if old_reports.resolve() == new_reports.resolve():
        return 0
    # granica katalogu: "/x/reports" nie moze pasowac do "/x/reports-old/..."
    old, new = str(old_reports).rstrip("/\\") + os.sep, str(new_reports).rstrip("/\\") + os.sep
    total = 0
    with db.connect() as con:
        for column in ("report_path", "report_json_path", "report_html_path"):
            changed = con.execute(
                f"SELECT COUNT(*) FROM runs WHERE {column} IS NOT NULL AND starts_with({column}, ?)",  # noqa: S608
                [old]).fetchone()[0]
            if changed:
                con.execute(
                    f"UPDATE runs SET {column} = ? || substr({column}, ?) "  # noqa: S608
                    f"WHERE {column} IS NOT NULL AND starts_with({column}, ?)",
                    [new, len(old) + 1, old])
                total += int(changed)
    return total


def _sha256_file(path_str: str | None) -> str | None:
    if not path_str:
        return None
    path = Path(path_str)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _backfill_run_records(settings: Settings, paths: TenantPaths, report: MigrationReport) -> None:
    engine = create_engine(settings)
    try:
        async with make_sessionmaker(engine)() as db:
            org = await service.ensure_default_org(db, retention_days=settings.daas_retention_days)
            known = set((await db.execute(select(RunRecord.run_id).where(RunRecord.org_id == org.id))).scalars())
            rows = DuckDBClient(paths.duckdb).list_runs(1_000_000)
            for row in rows:
                if row["run_id"] in known:
                    continue
                started = row.get("started_at")
                created = started.replace(tzinfo=UTC) if isinstance(started, datetime) and started.tzinfo is None \
                    else (started if isinstance(started, datetime) else datetime.now(UTC))
                status = row["run_status"].value if hasattr(row["run_status"], "value") else str(row["run_status"])
                db.add(RunRecord(
                    run_id=row["run_id"], org_id=org.id, pipeline=row["pipeline"], status=status,
                    sha256_html=_sha256_file(row.get("report_html_path")),
                    sha256_md=_sha256_file(row.get("report_path")),
                    sha256_json=_sha256_file(row.get("report_json_path")),
                    created_at=created))
                report.backfilled_runs += 1
            await db.commit()
    finally:
        await engine.dispose()


def migrate_runtime(settings: Settings, *, dry_run: bool = False) -> MigrationReport:
    """Wykonuje migracje. Blokujace (uzywa wlasnej petli zdarzen) — nie wolac z dzialajacej petli."""
    report = MigrationReport(dry_run=dry_run)
    target = TenantPaths.for_org(settings, "default")
    old_reports = settings.reports_path

    db_file = settings.duckdb_file
    if db_file.resolve() != target.duckdb.resolve():
        _move_file(db_file, target.duckdb, "daas.duckdb", report, dry_run)
        _move_file(db_file.with_name(db_file.name + ".wal"), target.duckdb.with_name("daas.duckdb.wal"),
                   "daas.duckdb.wal", report, dry_run)
    _move_dir_children(old_reports, target.reports, "reports", report, dry_run)
    _move_dir_children(settings.uploads_path, target.uploads, "uploads", report, dry_run)
    for name in LEGACY_AUDIT_NAMES:
        legacy = settings.runtime_dir / name
        if legacy.resolve() != target.audit_log.resolve():
            _move_file(legacy, target.audit_log, name, report, dry_run)

    if dry_run:
        return report

    upgrade_sync(settings)
    # baza docelowa, ktorej nie przenieslismy (konflikt), nie jest nasza: nie dotykamy jej
    db_is_ours = target.duckdb.exists() and "daas.duckdb" not in report.conflicts
    if db_is_ours:
        report.rewritten_paths = _rewrite_report_paths(DuckDBClient(target.duckdb), old_reports, target.reports)
        asyncio.run(_backfill_run_records(settings, target, report))
    log.info("migrate-runtime finished moved=%d conflicts=%d", len(report.moved), len(report.conflicts))
    return report
