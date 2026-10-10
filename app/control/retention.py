"""Retencja danych klienta: kasowanie po terminie (RODO: ograniczenie przechowywania).

Co jest usuwane dla organizacji:
    * uploady po `Upload.retention_until` (plik + rekord; audyt `upload.purged`). Upload czekajacy w kolejce zadan
      (`queued`/`running`) jest pomijany do czasu zakonczenia zadania,
    * pliki raportow runow starszych niz `Org.retention_days` (od `RunRecord.created_at`). `RunRecord` i hashe
      zostaja (`POST /verify` dziala dalej), a `report_purged_at` oznacza, ze pliku juz nie ma; audyt `report.purged`,
    * wiersze tenantowego DuckDB tych runow oraz wszystkie z `runs.started_at` starszym niz retencja (tabele
      `runs`, `cs2_matches`, `ecommerce_orders`, `uksc_hosts|checks|inventory` maja klucz `run_id`).
Uwaga: kasowanie wierszy `uksc_*` oznacza utrate porownania "poprzedni run" dla najstarszych runow — to zamierzone.
Zasady bezpieczenstwa: usuwane sa wylacznie pliki lezace w `uploads/` albo `reports/` danego najemcy (sciezka
rozwiazana i sprawdzona), nazwy plikow klienta nie trafiaja do audytu. Operacja jest idempotentna; `dry_run` tylko liczy.
Zamek per organizacja (`TenantRegistry.lock`) wyklucza rownoczesny run tej organizacji.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.control.audit import record_event
from app.control.executor import effective_llm
from app.control.models import Job, Org, RunRecord, Upload
from app.control.tenancy import InvalidOrgId, TenantPaths, TenantRegistry
from app.control.types import utcnow
from app.core.config import Settings

log = logging.getLogger(__name__)

#: Tabele tenantowego DuckDB z kolumna `run_id` (kolejnosc: dane, potem `runs`).
RUN_TABLES = ("cs2_matches", "ecommerce_orders", "uksc_checks", "uksc_inventory", "uksc_hosts", "runs")
REPORT_PATH_COLUMNS = ("report_path", "report_json_path", "report_html_path")


@dataclass
class PurgeReport:
    org_id: str
    uploads: int = 0      # usuniete (lub, w dry-run, do usuniecia) uploady
    reports: int = 0      # runy, ktorym usunieto pliki raportow
    files: int = 0        # usuniete pliki raportow
    rows: int = 0         # usuniete wiersze runow w tenantowym DuckDB
    dry_run: bool = False

    def line(self) -> str:
        mode = "dry-run " if self.dry_run else ""
        return (f"{mode}org={self.org_id} uploads={self.uploads} reports={self.reports} "
                f"files={self.files} rows={self.rows}")


def _unlink_inside(root: Path, candidate: Path, *, dry_run: bool) -> bool:
    """Usuwa plik tylko, gdy po rozwiazaniu symlinkow lezy w `root`. True = plik istnial (i zostal/zostalby usuniety)."""
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
            return False
        if not dry_run:
            resolved.unlink(missing_ok=True)
        return True
    except OSError:
        log.exception("retention: could not remove a file")
        return False


def _purge_uploads_files(paths: TenantPaths, names: list[str], dry_run: bool) -> None:
    for name in names:
        _unlink_inside(paths.uploads, paths.uploads / name, dry_run=dry_run)


def _purge_tenant(db, reports_root: Path, run_ids: list[str], cutoff: datetime, dry_run: bool) -> tuple[int, int]:
    """Pliki raportow i wiersze DuckDB. Zwraca `(usuniete pliki, usuniete wiersze `runs`)`."""
    files = rows = 0
    with db.connect() as con:
        old = [r[0] for r in con.execute("SELECT run_id FROM runs WHERE started_at < ?", [cutoff]).fetchall()]
        targets = sorted(set(run_ids) | set(old))
        for run_id in targets:
            row = con.execute(f"SELECT {', '.join(REPORT_PATH_COLUMNS)} FROM runs WHERE run_id = ?",  # noqa: S608
                              [run_id]).fetchone()
            for raw in (row or ()):
                if raw and _unlink_inside(reports_root, Path(raw), dry_run=dry_run):
                    files += 1
        if targets:
            rows = con.execute(f"SELECT count(*) FROM runs WHERE run_id IN ({', '.join('?' * len(targets))})",  # noqa: S608
                               targets).fetchone()[0]
            if not dry_run:
                for table in RUN_TABLES:
                    con.execute(f"DELETE FROM {table} WHERE run_id IN ({', '.join('?' * len(targets))})",  # noqa: S608
                                targets)
    return files, rows


async def purge_org(org: Org, sessionmaker: async_sessionmaker[AsyncSession], registry: TenantRegistry,
                    settings: Settings, *, dry_run: bool = False, now: datetime | None = None) -> PurgeReport:
    """Retencja jednej organizacji (zob. docstring modulu). Nie rzuca przy braku katalogu danych najemcy."""
    now = now or utcnow()
    report = PurgeReport(org_id=org.id, dry_run=dry_run)
    try:
        paths = TenantPaths.for_org(settings, org.id)
    except InvalidOrgId:
        log.warning("retention skipped: invalid org id")
        return report
    cutoff = now - timedelta(days=org.retention_days)
    async with registry.lock(org.id):
        # ---- uploady
        async with sessionmaker() as db:
            busy = select(Job.upload_id).where(Job.org_id == org.id, Job.status.in_(("queued", "running")),
                                               Job.upload_id.is_not(None))
            expired = (await db.execute(select(Upload).where(
                Upload.org_id == org.id, Upload.retention_until < now, Upload.id.not_in(busy)))).scalars().all()
            report.uploads = len(expired)
            if expired and not dry_run:
                await asyncio.to_thread(_purge_uploads_files, paths, [u.path for u in expired], False)
                for upload in expired:
                    await record_event(db, None, org_id=org.id, actor_type="system", actor_id=None,
                                       action="upload.purged", target=f"upload:{upload.id}", settings=settings,
                                       meta={"upload_id": upload.id, "kind": upload.kind, "bytes": upload.bytes})
                await db.execute(delete(Upload).where(Upload.id.in_([u.id for u in expired])))
                await db.commit()
            # ---- raporty i dane najemcy
            old_runs = (await db.execute(select(RunRecord.run_id).where(
                RunRecord.org_id == org.id, RunRecord.created_at < cutoff,
                RunRecord.report_purged_at.is_(None)))).scalars().all()
        if paths.root.exists():
            runner = await registry.aget(org.id, llm_narrative=effective_llm(settings, org))
            report.files, report.rows = await asyncio.to_thread(
                _purge_tenant, runner.db, paths.reports, list(old_runs), cutoff.replace(tzinfo=None), dry_run)
        report.reports = len(old_runs)
        if old_runs and not dry_run:
            async with sessionmaker() as db:
                await db.execute(update(RunRecord).where(RunRecord.run_id.in_(old_runs)).values(
                    report_purged_at=now))
                await record_event(db, None, org_id=org.id, actor_type="system", actor_id=None,
                                   action="report.purged", target=f"org:{org.id}", settings=settings,
                                   meta={"count": len(old_runs)})
                await db.commit()
    if any((report.uploads, report.reports, report.rows)):
        log.info("retention %s", report.line())
    return report


async def purge_all(sessionmaker: async_sessionmaker[AsyncSession], registry: TenantRegistry, settings: Settings,
                    *, org_id: str | None = None, dry_run: bool = False) -> list[PurgeReport]:
    """Retencja wszystkich organizacji (albo jednej). Blad jednej organizacji nie zatrzymuje pozostalych."""
    async with sessionmaker() as db:
        query = select(Org).order_by(Org.id)
        if org_id:
            query = query.where(Org.id == org_id)
        orgs = (await db.execute(query)).scalars().all()
    reports: list[PurgeReport] = []
    for org in orgs:
        try:
            reports.append(await purge_org(org, sessionmaker, registry, settings, dry_run=dry_run))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("retention failed for org_id=%s", org.id)
    return reports


async def retention_loop(sessionmaker, registry, settings: Settings, *, interval_sec: float) -> None:
    """Petla w lifespanie (tylko cloud): pierwszy przebieg po jednym interwale, bledy sa logowane i nie przerywaja petli."""
    while True:
        await asyncio.sleep(interval_sec)
        try:
            await purge_all(sessionmaker, registry, settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("retention cycle failed")
