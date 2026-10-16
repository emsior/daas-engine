"""Data plane: TenantPaths, ustawienia najemcy, rejestr runnerow (LRU, zamki, izolacja plikow)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.control.tenancy import InvalidOrgId, TenantPaths, TenantRegistry, tenant_settings
from app.core.config import Settings
from app.core.models import RunRequest


@pytest.fixture()
def base(settings: Settings) -> Settings:
    return settings


# ---------------------------------------------------------------- TenantPaths
def test_paths_layout(base: Settings):
    p = TenantPaths.for_org(base, "01HXYZ")
    root = base.tenants_root.resolve() / "01HXYZ"
    assert (p.root, p.duckdb, p.uploads, p.reports, p.audit_log) == (
        root, root / "daas.duckdb", root / "uploads", root / "reports", root / "audit.jsonl")


@pytest.mark.parametrize("org_id", ["default", "01JABCDEFGHJKMNPQRSTVWXYZ0", "a", "A-b_c", "x" * 64])
def test_valid_org_ids(base: Settings, org_id: str):
    assert TenantPaths.for_org(base, org_id).root.name == org_id


@pytest.mark.parametrize("org_id", ["", "..", ".", "../x", "a/b", "a\\b", ".hidden", "x" * 65, "a b", "a\x00b",
                                    "/etc", "ł", "a.b", "-lead", "_lead"])
def test_invalid_org_ids_are_refused(base: Settings, org_id: str):
    with pytest.raises(InvalidOrgId):
        TenantPaths.for_org(base, org_id)


def test_non_string_org_id_is_refused(base: Settings):
    with pytest.raises(InvalidOrgId):
        TenantPaths.for_org(base, None)  # type: ignore[arg-type]


def test_symlinked_tenant_directory_cannot_escape(base: Settings, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base.tenants_root.mkdir(parents=True, exist_ok=True)
    (base.tenants_root / "evil").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidOrgId):
        TenantPaths.for_org(base, "evil")


def test_ensure_creates_directories(base: Settings):
    p = TenantPaths.for_org(base, "org1").ensure()
    assert p.uploads.is_dir() and p.reports.is_dir() and not p.duckdb.exists()


def test_contains_guards_the_uploads_directory(base: Settings):
    p = TenantPaths.for_org(base, "org1").ensure()
    assert p.contains(p.uploads / "a.csv")
    assert not p.contains(p.reports / "a.csv")
    assert not p.contains(p.uploads / ".." / "daas.duckdb")
    other = TenantPaths.for_org(base, "org2").ensure()
    assert not p.contains(other.uploads / "a.csv")


def test_contains_rejects_symlink_out_of_uploads(base: Settings, tmp_path: Path):
    p = TenantPaths.for_org(base, "org1").ensure()
    secret = tmp_path / "secret.csv"
    secret.write_text("x")
    link = p.uploads / "link.csv"
    link.symlink_to(secret)
    assert not p.contains(link)


# ---------------------------------------------------------------- tenant_settings
def test_tenant_settings_redirect_all_paths(base: Settings):
    p = TenantPaths.for_org(base, "org1")
    s = tenant_settings(base, p)
    assert s.duckdb_file == p.duckdb and s.reports_path == p.reports
    assert s.uploads_path == p.uploads and s.audit_log_path == p.audit_log


def test_tenant_settings_do_not_mutate_the_base(base: Settings):
    before = base.duckdb_file
    tenant_settings(base, TenantPaths.for_org(base, "org1"), llm_narrative=True)
    assert base.duckdb_file == before and base.daas_llm_narrative is False


def test_tenant_settings_llm_flag_override(base: Settings):
    p = TenantPaths.for_org(base, "org1")
    assert tenant_settings(base, p, llm_narrative=True).daas_llm_narrative is True
    assert tenant_settings(base, p).daas_llm_narrative is base.daas_llm_narrative


# ---------------------------------------------------------------- rejestr
def test_registry_is_lazy(base: Settings):
    reg = TenantRegistry(base)
    assert reg.active() == [] and not base.tenants_root.exists()
    reg.get("org1")
    assert reg.active() == ["org1"] and (base.tenants_root / "org1" / "daas.duckdb").is_file()


def test_registry_returns_the_same_runner(base: Settings):
    reg = TenantRegistry(base)
    assert reg.get("org1") is reg.get("org1") and reg.get("org1") is not reg.get("org2")


def test_registry_lru_eviction(base: Settings):
    reg = TenantRegistry(base, max_active=2)
    reg.get("a")
    reg.get("b")
    reg.get("a")  # a jest teraz najswiezszy
    reg.get("c")
    assert reg.active() == ["a", "c"] and reg.evictions == 1


def test_registry_does_not_evict_tenant_with_active_run(base: Settings):
    reg = TenantRegistry(base, max_active=1)
    reg.get("a")
    reg.mark_in_use("a")
    reg.get("b")
    assert set(reg.active()) == {"a", "b"}  # limit chwilowo przekroczony zamiast zamykac trwajacy run
    reg.release("a")
    reg.get("c")
    assert "a" not in reg.active()


def test_registry_evict_skips_busy_tenant(base: Settings):
    reg = TenantRegistry(base)
    reg.get("a")
    reg.mark_in_use("a")
    reg.evict("a")
    assert reg.active() == ["a"]
    reg.release("a")
    reg.evict("a")
    assert reg.active() == []


def test_registry_rebuilds_runner_when_llm_flag_changes(base: Settings):
    reg = TenantRegistry(base)
    first = reg.get("a", llm_narrative=False)
    assert reg.get("a", llm_narrative=False) is first
    second = reg.get("a", llm_narrative=True)
    assert second is not first and second.settings.daas_llm_narrative is True


def test_registry_none_means_any_flag_and_keeps_the_runner(base: Settings):
    """C8: odczyt API (`get(org)`) nie przebudowuje runnera ustawionego przez executor (`llm_narrative=False`)."""
    reg = TenantRegistry(base)
    built = reg.get("a", llm_narrative=False)
    assert reg.get("a") is built
    assert reg.get("a", llm_narrative=None) is built
    assert reg.get("a", llm_narrative=False) is built
    # jawna zmiana flagi nadal przebudowuje, a kolejny odczyt "dowolny" dostaje juz nowy runner
    rebuilt = reg.get("a", llm_narrative=True)
    assert rebuilt is not built and reg.get("a") is rebuilt


def test_registry_none_first_then_explicit_flag_rebuilds(base: Settings):
    reg = TenantRegistry(base)
    anonymous = reg.get("a")
    assert reg.get("a") is anonymous
    explicit = reg.get("a", llm_narrative=False)
    assert explicit is not anonymous and reg.get("a") is explicit


def test_registry_rejects_invalid_ids(base: Settings):
    with pytest.raises(InvalidOrgId):
        TenantRegistry(base).get("../x")


def test_registry_forget_drops_runner_and_lock(base: Settings):
    async def go():
        reg = TenantRegistry(base)
        reg.get("a")
        lock = reg.lock("a")
        reg.forget("a")
        return reg.active(), reg.lock("a") is lock

    assert asyncio.run(go()) == ([], False)


def test_registry_lock_is_per_org(base: Settings):
    async def go():
        reg = TenantRegistry(base)
        return reg.lock("a") is reg.lock("a"), reg.lock("a") is reg.lock("b")

    assert asyncio.run(go()) == (True, False)


def test_registry_aget_runs_in_a_thread(base: Settings):
    async def go():
        return await TenantRegistry(base).aget("a")

    assert asyncio.run(go()).settings.duckdb_file.name == "daas.duckdb"


def test_registry_close_all(base: Settings):
    reg = TenantRegistry(base)
    reg.get("a")
    reg.close_all()
    assert reg.active() == []


# ---------------------------------------------------------------- izolacja plikow
def test_two_tenants_never_share_files(base: Settings):
    reg = TenantRegistry(base)
    a, b = reg.get("org-a"), reg.get("org-b")
    result = a.run(RunRequest(pipeline="ecommerce_demo"))
    assert a.db.get_run(result.run_id) is not None and b.db.get_run(result.run_id) is None
    assert b.db.list_runs() == []
    assert Path(result.report_html_path).resolve().is_relative_to(reg.paths("org-a").reports)
    assert not list(reg.paths("org-b").reports.iterdir())


def test_audit_files_are_per_tenant(base: Settings):
    reg = TenantRegistry(base)
    a, _b = reg.get("org-a"), reg.get("org-b")
    mine = reg.paths("org-a").uploads / ("c" * 32 + ".csv")
    mine.write_bytes((Path(base.fixtures_path) / "client_export_pl.csv").read_bytes())
    result = a.run(RunRequest(pipeline="ecommerce_demo", source_path=str(mine)))
    assert result.run_status.value in {"SUCCESS", "PARTIAL"}
    assert reg.paths("org-a").audit_log.exists()  # policy zapisuje decyzje przy source_path
    assert not reg.paths("org-b").audit_log.exists()


def test_source_outside_tenant_uploads_is_blocked(base: Settings, tmp_path: Path):
    reg = TenantRegistry(base)
    a = reg.get("org-a")
    b_uploads = reg.paths("org-b").ensure().uploads
    foreign = b_uploads / ("a" * 32 + ".csv")
    foreign.write_text("date,revenue\n2026-01-05,100\n", encoding="utf-8")
    result = a.run(RunRequest(pipeline="ecommerce_demo", source_path=str(foreign)))
    assert result.run_status.value == "FAILED"


def test_uksc_source_outside_tenant_uploads_is_blocked(base: Settings):
    reg = TenantRegistry(base)
    a = reg.get("org-a")
    foreign = reg.paths("org-b").ensure().uploads / ("b" * 32 + ".json")
    foreign.write_text("{}", encoding="utf-8")
    result = a.run(RunRequest(pipeline="uksc_evidence", source_path=str(foreign)))
    assert result.run_status.value == "FAILED"
