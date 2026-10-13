"""Retencja (H-1): uploady po `retention_until`, raporty starsze niz `retention_days`, wiersze DuckDB; dry-run, idempotencja."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control.db import create_engine, make_sessionmaker
from app.control.retention import PurgeReport, purge_all
from app.control.tenancy import TenantPaths, TenantRegistry
from app.control.types import utcnow
from app.core.config import Settings
from tests.conftest import PROJECT_ROOT
from tests.test_jobs import hold_executor, seed_job, upload, wait_terminal
from tests.v1_helpers import API, get_rows, key_headers, seed_org, update_rows

CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def do_purge(settings: Settings, org_id: str | None = None, *, dry_run: bool = False) -> list[PurgeReport]:
    """Jeden przebieg retencji na wlasnym silniku (jak komenda CLI)."""
    async def go():
        engine = create_engine(settings)
        registry = TenantRegistry(settings)
        try:
            return await purge_all(make_sessionmaker(engine), registry, settings, org_id=org_id, dry_run=dry_run)
        finally:
            registry.close_all()
            await engine.dispose()

    return asyncio.run(go())


def mine(reports: list[PurgeReport], org_id: str) -> PurgeReport:
    return next(r for r in reports if r.org_id == org_id)


def expire_upload(settings: Settings, upload_id: str) -> None:
    update_rows(settings, models.Upload, {"retention_until": utcnow() - timedelta(days=1)},
                models.Upload.id == upload_id)


def upload_files(settings: Settings, org_id: str) -> list[Path]:
    return sorted(TenantPaths.for_org(settings, org_id).uploads.glob("*.csv"))


def finished_run(client: TestClient, org) -> dict:
    r = client.post(f"{API}/orgs/{org.org_id}/jobs", headers=key_headers(org.api_key),
                    json={"pipeline": "cs2_demo", "force_mock": True})
    job = wait_terminal(client, org, r.json()["id"])
    assert job["status"] == "succeeded"
    return job


def report_files(settings: Settings, org_id: str) -> list[Path]:
    return [p for p in TenantPaths.for_org(settings, org_id).reports.rglob("*") if p.is_file()]


def age_run(settings: Settings, run_id: str, days: int = 120) -> None:
    update_rows(settings, models.RunRecord, {"created_at": utcnow() - timedelta(days=days)},
                models.RunRecord.run_id == run_id)


# ================================================================ uploady
def test_expired_upload_is_removed_with_its_record_and_audited(cloud_client, cloud_settings, org):
    upload_id = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    keep_id = upload(cloud_client, org, "inne.csv", CSV_BYTES)
    expire_upload(cloud_settings, upload_id)
    assert len(upload_files(cloud_settings, org.org_id)) == 2
    report = mine(do_purge(cloud_settings), org.org_id)
    assert report.uploads == 1 and report.dry_run is False
    assert [p.stem for p in upload_files(cloud_settings, org.org_id)] == [keep_id]
    assert [u.id for u in get_rows(cloud_settings, models.Upload)] == [keep_id]
    (event,) = get_rows(cloud_settings, models.AuditEvent, models.AuditEvent.action == "upload.purged")
    assert event.org_id == org.org_id and event.actor_type == "system"
    assert event.meta["upload_id"] == upload_id and "dane.csv" not in str(event.meta)


def test_a_job_on_a_purged_upload_cannot_be_started(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    upload_id = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    expire_upload(cloud_settings, upload_id)
    do_purge(cloud_settings)
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/jobs", headers=key_headers(org.api_key),
                          json={"pipeline": "ecommerce_demo", "upload_id": upload_id})
    assert r.status_code == 404


def test_an_upload_waiting_in_the_queue_is_not_purged(cloud_client, cloud_settings, org):
    hold_executor(cloud_client)
    upload_id = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/jobs", headers=key_headers(org.api_key),
                          json={"pipeline": "ecommerce_demo", "upload_id": upload_id})
    assert r.status_code == 202
    expire_upload(cloud_settings, upload_id)
    assert mine(do_purge(cloud_settings), org.org_id).uploads == 0
    assert len(upload_files(cloud_settings, org.org_id)) == 1


def test_unexpired_uploads_are_untouched(cloud_client, cloud_settings, org):
    upload(cloud_client, org, "dane.csv", CSV_BYTES)
    assert mine(do_purge(cloud_settings), org.org_id).uploads == 0
    assert len(upload_files(cloud_settings, org.org_id)) == 1


def test_a_missing_file_does_not_block_removing_the_record(cloud_client, cloud_settings, org):
    upload_id = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    upload_files(cloud_settings, org.org_id)[0].unlink()
    expire_upload(cloud_settings, upload_id)
    assert mine(do_purge(cloud_settings), org.org_id).uploads == 1
    assert not get_rows(cloud_settings, models.Upload)


# ================================================================ raporty i dane najemcy
def test_old_reports_are_deleted_but_the_run_record_and_hashes_stay(cloud_client, cloud_settings, org):
    job = finished_run(cloud_client, org)
    fresh = finished_run(cloud_client, org)
    before = report_files(cloud_settings, org.org_id)
    assert before
    age_run(cloud_settings, job["run_id"])
    report = mine(do_purge(cloud_settings), org.org_id)
    assert report.reports == 1 and report.files >= 1
    (old,) = get_rows(cloud_settings, models.RunRecord, models.RunRecord.run_id == job["run_id"])
    (new,) = get_rows(cloud_settings, models.RunRecord, models.RunRecord.run_id == fresh["run_id"])
    assert old.report_purged_at is not None and new.report_purged_at is None
    assert old.sha256_html and old.sha256_md and old.sha256_json            # hashe zostaja
    after = report_files(cloud_settings, org.org_id)
    assert len(after) < len(before) and all(job["run_id"] not in p.name for p in after)
    # "sprawdz sam" dziala dalej: skrot starego raportu jest nadal znany
    r = cloud_client.post(f"{API}/orgs/{org.org_id}/verify", json={"sha256": old.sha256_md},
                          headers=key_headers(org.api_key))
    assert r.json()["known"] is True and r.json()["run_id"] == job["run_id"]
    gone = cloud_client.get(f"{API}/orgs/{org.org_id}/runs/{job['run_id']}/report", params={"fmt": "md"},
                            headers=key_headers(org.api_key))
    assert gone.status_code in (404, 410)
    ok = cloud_client.get(f"{API}/orgs/{org.org_id}/runs/{fresh['run_id']}/report", params={"fmt": "md"},
                          headers=key_headers(org.api_key))
    assert ok.status_code == 200
    assert "report.purged" in [e.action for e in get_rows(cloud_settings, models.AuditEvent)]


def test_tenant_database_rows_of_old_runs_are_deleted(cloud_client, cloud_settings, org):
    import duckdb

    job = finished_run(cloud_client, org)
    fresh = finished_run(cloud_client, org)
    db_path = TenantPaths.for_org(cloud_settings, org.org_id).duckdb

    def counts() -> dict[str, int]:
        con = duckdb.connect(str(db_path))
        try:
            return {rid: con.execute("SELECT (SELECT count(*) FROM runs WHERE run_id = ?) + "
                                     "(SELECT count(*) FROM cs2_matches WHERE run_id = ?)", [rid, rid]).fetchone()[0]
                    for rid in (job["run_id"], fresh["run_id"])}
        finally:
            con.close()

    first = counts()
    assert first[job["run_id"]] > 0 and first[fresh["run_id"]] > 0
    age_run(cloud_settings, job["run_id"])
    do_purge(cloud_settings)
    second = counts()
    assert second[job["run_id"]] == 0 and second[fresh["run_id"]] > 0


def test_rows_older_than_retention_are_deleted_even_without_a_run_record(cloud_client, cloud_settings, org):
    import duckdb

    job = finished_run(cloud_client, org)
    update_rows(cloud_settings, models.Org, {"retention_days": 30}, models.Org.id == org.org_id)
    db_path = TenantPaths.for_org(cloud_settings, org.org_id).duckdb
    con = duckdb.connect(str(db_path))
    con.execute("UPDATE runs SET started_at = started_at - INTERVAL 60 DAY WHERE run_id = ?", [job["run_id"]])
    con.close()
    do_purge(cloud_settings)
    con = duckdb.connect(str(db_path))
    try:
        assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM cs2_matches").fetchone()[0] == 0
    finally:
        con.close()


def test_retention_days_of_the_org_decide(cloud_client, cloud_settings, org):
    job = finished_run(cloud_client, org)
    age_run(cloud_settings, job["run_id"], days=40)
    assert mine(do_purge(cloud_settings), org.org_id).reports == 0         # retencja 90 dni
    update_rows(cloud_settings, models.Org, {"retention_days": 30}, models.Org.id == org.org_id)
    assert mine(do_purge(cloud_settings), org.org_id).reports == 1


# ================================================================ dry-run, idempotencja, izolacja
def test_dry_run_reports_but_deletes_nothing(cloud_client, cloud_settings, org):
    upload_id = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    expire_upload(cloud_settings, upload_id)
    job = finished_run(cloud_client, org)
    age_run(cloud_settings, job["run_id"])
    files_before = report_files(cloud_settings, org.org_id)
    report = mine(do_purge(cloud_settings, dry_run=True), org.org_id)
    assert report.dry_run is True and report.uploads == 1 and report.reports == 1
    assert len(upload_files(cloud_settings, org.org_id)) == 1
    assert report_files(cloud_settings, org.org_id) == files_before
    assert len(get_rows(cloud_settings, models.Upload)) == 1
    (rec,) = get_rows(cloud_settings, models.RunRecord)
    assert rec.report_purged_at is None
    assert not [e for e in get_rows(cloud_settings, models.AuditEvent) if e.action.endswith(".purged")]


def test_purge_is_idempotent(cloud_client, cloud_settings, org):
    upload_id = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    expire_upload(cloud_settings, upload_id)
    job = finished_run(cloud_client, org)
    age_run(cloud_settings, job["run_id"])
    first = mine(do_purge(cloud_settings), org.org_id)
    events = len(get_rows(cloud_settings, models.AuditEvent))
    second = mine(do_purge(cloud_settings), org.org_id)
    assert (first.uploads, first.reports) == (1, 1)
    assert (second.uploads, second.reports, second.files, second.rows) == (0, 0, 0, 0)
    assert len(get_rows(cloud_settings, models.AuditEvent)) == events


def test_purge_can_be_limited_to_one_organization(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    for o in (org, other):
        expire_upload(cloud_settings, upload(cloud_client, o, "dane.csv", CSV_BYTES))
    reports = do_purge(cloud_settings, org_id=org.org_id)
    assert [r.org_id for r in reports] == [org.org_id]
    assert not upload_files(cloud_settings, org.org_id) and len(upload_files(cloud_settings, other.org_id)) == 1
    assert len(get_rows(cloud_settings, models.Upload)) == 1


def test_purge_all_covers_every_organization(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    for o in (org, other):
        expire_upload(cloud_settings, upload(cloud_client, o, "dane.csv", CSV_BYTES))
    reports = do_purge(cloud_settings)
    assert {r.org_id for r in reports} >= {org.org_id, other.org_id}
    assert not get_rows(cloud_settings, models.Upload)


def test_purge_never_touches_files_outside_the_tenant_directory(cloud_client, cloud_settings, org, tmp_path):
    outside = tmp_path / "precious.csv"
    outside.write_text("keep")
    upload_id = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    expire_upload(cloud_settings, upload_id)
    update_rows(cloud_settings, models.Upload, {"path": "../../../../precious.csv"}, models.Upload.id == upload_id)
    do_purge(cloud_settings)
    assert outside.read_text() == "keep"


# ================================================================ C11/N2: przeniesiony runtime i wspolny resolver sciezek
def rewrite_report_paths(client: TestClient, org, run_id: str, prefix: str) -> None:
    db = client.app.state.tenants.get(org.org_id).db
    with db.connect() as con:
        con.execute(
            "UPDATE runs SET report_path = ? || regexp_extract(report_path, '[^/]+$'), "
            "report_json_path = ? || regexp_extract(report_json_path, '[^/]+$'), "
            "report_html_path = ? || regexp_extract(report_html_path, '[^/]+$') WHERE run_id = ?",
            [prefix, prefix, prefix, run_id])


@pytest.mark.parametrize("prefix", ["/srv/old-host/runtime/tenants/xyz/reports/", "C:\\\\daas\\\\runtime\\\\"])
def test_purge_removes_reports_by_basename_after_the_runtime_directory_moved(cloud_client, cloud_settings, org, prefix):
    job = finished_run(cloud_client, org)
    fresh = finished_run(cloud_client, org)
    rewrite_report_paths(cloud_client, org, job["run_id"], prefix)
    if "\\" in prefix:
        db = cloud_client.app.state.tenants.get(org.org_id).db
        with db.connect() as con:
            con.execute("UPDATE runs SET report_path = replace(report_path, '/', '\\') WHERE run_id = ?", [job["run_id"]])
    age_run(cloud_settings, job["run_id"])
    report = mine(do_purge(cloud_settings), org.org_id)
    assert report.reports == 1 and report.files == 3
    left = report_files(cloud_settings, org.org_id)
    assert left and all(job["run_id"][:8] not in p.name for p in left)
    assert any(fresh["run_id"][:8] in p.name for p in left)           # swiezy run nietkniety


def test_purge_never_follows_a_stored_path_outside_the_reports_directory(cloud_client, cloud_settings, org, tmp_path):
    job = finished_run(cloud_client, org)
    outside = tmp_path / "precious.md"
    outside.write_text("keep")
    sibling = TenantPaths.for_org(cloud_settings, org.org_id).uploads / "dane.csv"     # inny katalog tego najemcy
    sibling.write_text("keep too")
    db = cloud_client.app.state.tenants.get(org.org_id).db
    with db.connect() as con:
        con.execute("UPDATE runs SET report_path = ?, report_json_path = ?, report_html_path = ? WHERE run_id = ?",
                    [str(outside), "../../../../precious.md", str(sibling), job["run_id"]])
    age_run(cloud_settings, job["run_id"])
    mine(do_purge(cloud_settings), org.org_id)
    assert outside.read_text() == "keep" and sibling.read_text() == "keep too"


def test_purge_does_not_follow_a_symlink_out_of_the_reports_directory(cloud_client, cloud_settings, org, tmp_path):
    job = finished_run(cloud_client, org)
    target = tmp_path / "secret.md"
    target.write_text("keep")
    md = next(p for p in report_files(cloud_settings, org.org_id) if p.suffix == ".md" and job["run_id"][:8] in p.name)
    md.unlink()
    md.symlink_to(target)
    age_run(cloud_settings, job["run_id"])
    mine(do_purge(cloud_settings), org.org_id)
    assert target.read_text() == "keep"


def test_runs_and_retention_share_one_resolver():
    from app.api.v1 import runs
    from app.control import report_paths, retention

    assert runs._resolve_report is report_paths.resolve_report
    assert retention.resolve_report is report_paths.resolve_report


# ================================================================ CLI i petla
def test_cli_purge_dry_run_and_real(cloud_client, cloud_settings, org, capsys):
    from app.cli import main
    from app.core.config import reset_settings_cache

    upload_id = upload(cloud_client, org, "dane.csv", CSV_BYTES)
    expire_upload(cloud_settings, upload_id)
    reset_settings_cache()
    assert main(["purge", "--dry-run", "--org", org.org_id]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out and org.org_id in out and "uploads=1" in out
    assert len(upload_files(cloud_settings, org.org_id)) == 1
    assert main(["purge", "--org", org.org_id]) == 0
    assert not upload_files(cloud_settings, org.org_id)


def test_cli_purge_unknown_org_is_an_error(cloud_client, cloud_settings, org):
    from app.cli import main
    from app.core.config import reset_settings_cache

    reset_settings_cache()
    assert main(["purge", "--org", "NOPE"]) == 1


def test_lifespan_starts_the_retention_loop_only_in_cloud(cloud_client, cloud_factory):
    assert cloud_client.app.state.retention_task is not None
    assert not cloud_client.app.state.retention_task.done()


def test_selfhost_has_no_retention_loop(client):
    assert client.app.state.retention_task is None


def test_each_lifespan_stops_its_own_retention_loop(cloud_settings, monkeypatch):
    """Dwa klienty jednej aplikacji = dwa lifespany (np. `second_browser`): kazdy zatrzymuje WLASNA petle retencji.

    Regresja z CI: zamkniecie zewnetrznego klienta siegalo po zadanie wewnetrznego (z jego zamknietej juz petli
    zdarzen) - Python 3.11 rzucal `RuntimeError: Event loop is closed`, nowsze wersje zostawialy zywa petle
    retencji zewnetrznego klienta do konca zamykania. Lifespan anuluje swoja petle PRZED zatrzymaniem executora.
    """
    from app.control.executor import JobExecutor
    from app.main import create_app

    alive_at_stop: list[int] = []
    original_stop = JobExecutor.stop

    async def stop(self) -> None:
        alive_at_stop.append(sum(1 for t in asyncio.all_tasks() if t.get_name() == "retention"))
        await original_stop(self)

    monkeypatch.setattr(JobExecutor, "stop", stop)
    app = create_app(cloud_settings)
    with TestClient(app, base_url="https://testserver"):
        outer = app.state.retention_task
        with TestClient(app, base_url="https://testserver"):
            inner = app.state.retention_task
            assert outer is not None and inner is not None and inner is not outer
        assert inner.done()
        assert not outer.done()             # zewnetrzny klient dziala dalej
    assert outer.done()
    assert alive_at_stop == [0, 0]          # wewnetrzny, potem zewnetrzny: zadna petla retencji nie przezyla


def test_the_loop_runs_a_cycle_and_survives_errors(cloud_client, cloud_settings, org):
    import app.control.retention as retention

    calls: list[int] = []

    async def fake_cycle(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return []

    async def go():
        original = retention.purge_all
        retention.purge_all = fake_cycle
        try:
            task = asyncio.create_task(retention.retention_loop(
                None, None, cloud_settings, interval_sec=0.01))
            await asyncio.sleep(0.2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            retention.purge_all = original

    asyncio.run(go())
    assert len(calls) >= 2          # po bledzie petla dziala dalej


def test_seed_job_helper_is_importable():  # utrzymuje import wspolnych pomocnikow
    assert callable(seed_job)
