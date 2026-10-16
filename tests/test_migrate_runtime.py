"""`migrate-runtime`: runtime/ sprzed 2.0 -> runtime/tenants/default/ (idempotentne, bez nadpisywania)."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import main as cli_main
from app.control import models
from app.control.db import create_engine, make_sessionmaker
from app.control.migrate_runtime import migrate_runtime
from app.core.config import Settings, reset_settings_cache
from app.core.models import RunRequest
from app.main import create_app
from app.pipelines.runner import PipelineRunner
from app.storage.duckdb_client import DuckDBClient
from tests.conftest import PROJECT_ROOT, auth_headers


@pytest.fixture()
def legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Uklad 1.x: runtime/daas.duckdb, runtime/reports, runtime/uploads, runtime/audit.jsonl z jednym runem."""
    runtime = tmp_path / "rt"
    monkeypatch.setenv("DUCKDB_PATH", str(runtime / "daas.duckdb"))
    monkeypatch.setenv("REPORTS_DIR", str(runtime / "reports"))
    monkeypatch.setenv("FIXTURES_DIR", str(PROJECT_ROOT / "data" / "fixtures"))
    reset_settings_cache()
    s = Settings(_env_file=None)
    s.ensure_dirs()
    s.uploads_path.mkdir(parents=True, exist_ok=True)
    upload = s.uploads_path / ("d" * 32 + ".csv")
    upload.write_bytes((PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes())
    runner = PipelineRunner(s, DuckDBClient(s.duckdb_file))
    result = runner.run(RunRequest(pipeline="ecommerce_demo", source_path=str(upload)))
    assert result.run_status.value in {"SUCCESS", "PARTIAL"}
    (runtime / "reports" / ".gitkeep").write_text("")
    return s, result


def _run_records(settings: Settings) -> list[models.RunRecord]:
    async def go():
        engine = create_engine(settings)
        try:
            async with make_sessionmaker(engine)() as db:
                return list((await db.execute(select(models.RunRecord))).scalars())
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_files_are_moved_to_the_default_tenant(legacy):
    s, _ = legacy
    report = migrate_runtime(s)
    target = s.tenants_root / "default"
    assert (target / "daas.duckdb").is_file() and not s.duckdb_file.exists()
    assert (target / "uploads" / ("d" * 32 + ".csv")).is_file() and not (s.uploads_path / ("d" * 32 + ".csv")).exists()
    assert (target / "audit.jsonl").is_file() and not (s.runtime_dir / "audit.jsonl").exists()
    assert list((target / "reports").glob("ecommerce_demo_latest.*"))
    assert report.moved and not report.conflicts


def test_legacy_directories_are_removed_when_emptied(legacy):
    s, _ = legacy
    migrate_runtime(s)
    assert not s.reports_path.exists() and not s.uploads_path.exists()


def test_gitkeep_is_not_carried_over(legacy):
    s, _ = legacy
    migrate_runtime(s)
    assert not (s.tenants_root / "default" / "reports" / ".gitkeep").exists()


def test_report_paths_in_database_point_to_the_new_location(legacy):
    s, result = legacy
    migrate_runtime(s)
    row = DuckDBClient(s.tenants_root / "default" / "daas.duckdb").get_run(result.run_id)
    for key in ("report_path", "report_json_path", "report_html_path"):
        path = Path(row[key])
        assert path.is_file() and path.resolve().is_relative_to((s.tenants_root / "default" / "reports").resolve())


def test_second_run_changes_nothing(legacy):
    s, _ = legacy
    first = migrate_runtime(s)
    second = migrate_runtime(s)
    assert first.changed and not second.changed
    assert second.moved == [] and second.rewritten_paths == 0 and second.backfilled_runs == 0
    assert second.conflicts == []
    third = migrate_runtime(s)
    assert not third.changed


def test_run_history_is_backfilled_with_report_hashes(legacy):
    s, result = legacy
    report = migrate_runtime(s)
    assert report.backfilled_runs == 1
    (record,) = _run_records(s)
    new_reports = s.tenants_root / "default" / "reports"
    assert (record.run_id, record.org_id, record.pipeline) == (result.run_id, "default", "ecommerce_demo")
    assert record.sha256_html == _sha(next(new_reports.glob(f"*{result.run_id}*.html"), None) or Path(
        DuckDBClient(s.tenants_root / "default" / "daas.duckdb").get_run(result.run_id)["report_html_path"]))
    assert record.sha256_md and record.sha256_json and len(record.sha256_html) == 64


def test_default_org_exists_after_migration(legacy):
    s, _ = legacy
    migrate_runtime(s)

    async def go():
        engine = create_engine(s)
        try:
            async with make_sessionmaker(engine)() as db:
                return (await db.get(models.Org, "default")).plan
        finally:
            await engine.dispose()

    assert asyncio.run(go()) == "selfhost"


def test_dry_run_changes_nothing(legacy):
    s, _ = legacy
    report = migrate_runtime(s, dry_run=True)
    assert report.moved and s.duckdb_file.exists() and not (s.tenants_root / "default").exists()
    assert any(line.startswith("would move") for line in report.lines())


def test_existing_target_is_a_conflict_not_an_overwrite(legacy):
    s, _ = legacy
    target = s.tenants_root / "default"
    target.mkdir(parents=True)
    (target / "daas.duckdb").write_bytes(b"precious")
    report = migrate_runtime(s)
    assert "daas.duckdb" in report.conflicts
    assert (target / "daas.duckdb").read_bytes() == b"precious" and s.duckdb_file.exists()


def test_report_file_conflict_leaves_the_source_in_place(legacy):
    s, _ = legacy
    target_reports = s.tenants_root / "default" / "reports"
    target_reports.mkdir(parents=True)
    name = next(p.name for p in s.reports_path.iterdir() if p.suffix == ".md")
    (target_reports / name).write_text("mine")
    report = migrate_runtime(s)
    assert f"reports/{name}" in report.conflicts
    assert (target_reports / name).read_text() == "mine" and (s.reports_path / name).exists()


def test_empty_runtime_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "empty" / "daas.duckdb"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "empty" / "reports"))
    reset_settings_cache()
    report = migrate_runtime(Settings(_env_file=None))
    assert not report.changed and not report.conflicts


def test_alternative_audit_file_name_is_migrated(legacy):
    s, _ = legacy
    (s.runtime_dir / "audit.jsonl").rename(s.runtime_dir / "audit.log")
    migrate_runtime(s)
    assert (s.tenants_root / "default" / "audit.jsonl").is_file()


def test_wal_file_is_planned_together_with_the_database(legacy):
    """Plik .wal idzie razem z baza (DuckDB po otwarciu sam go odtwarza, wiec sprawdzamy plan, nie plik)."""
    s, _ = legacy
    s.duckdb_file.with_name("daas.duckdb.wal").write_bytes(b"")
    report = migrate_runtime(s, dry_run=True)
    assert {"daas.duckdb", "daas.duckdb.wal"} <= set(report.moved)


def test_prefix_boundary_of_report_paths(legacy):
    """Sciezka z sasiedniego katalogu o wspolnym prefiksie ('reports-old') nie moze byc przepisana."""
    s, result = legacy
    other = s.runtime_dir / "reports-old" / "x.md"
    other.parent.mkdir()
    other.write_text("x")
    with DuckDBClient(s.duckdb_file).connect() as con:
        con.execute("UPDATE runs SET report_path = ? WHERE run_id = ?", [str(other), result.run_id])
    migrate_runtime(s)
    row = DuckDBClient(s.tenants_root / "default" / "daas.duckdb").get_run(result.run_id)
    assert row["report_path"] == str(other)


def test_cli_exit_code_and_output(legacy, capsys):
    s, _ = legacy
    assert cli_main(["migrate-runtime", "--dry-run"]) == 0
    assert "would move: daas.duckdb" in capsys.readouterr().out
    assert cli_main(["migrate-runtime"]) == 0
    assert "moved: daas.duckdb" in capsys.readouterr().out
    assert cli_main(["migrate-runtime"]) == 0
    assert "nothing changed" in capsys.readouterr().out


def test_cli_exit_code_is_1_on_conflict(legacy):
    s, _ = legacy
    target = s.tenants_root / "default"
    target.mkdir(parents=True)
    (target / "daas.duckdb").write_bytes(b"x")
    assert cli_main(["migrate-runtime"]) == 1


def test_application_serves_migrated_history_through_legacy_routes(legacy):
    s, result = legacy
    migrate_runtime(s)
    with TestClient(create_app(), headers=auth_headers()) as c:
        r = c.get(f"/reports/run/{result.run_id}?fmt=html")
        assert r.status_code == 200 and "<table>" in r.text
        assert c.get(f"/runs/{result.run_id}").json()["run_id"] == result.run_id
