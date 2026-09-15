"""Testy integracji warstwy policy w runtime (feature flag DAAS_POLICY_ENFORCE)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.models import RunRequest, RunStatus
from app.pipelines.runner import PipelineRunner


@pytest.fixture()
def isolated_env(tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir = runtime_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = runtime_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    duckdb_file = runtime_dir / "test.duckdb"

    # Przygotuj przykładowy plik wewnątrz workspace
    valid_csv = uploads_dir / "valid_sample.csv"
    valid_csv.write_text(
        "Data,ID zamówienia,Klient,Kwota,Status\n"
        "2026-03-01,ORD-001,Klient A,150.00,Zrealizowane\n",
        encoding="utf-8",
    )

    # Przygotuj plik na zewnątrz workspace
    external_dir = tmp_path / "external"
    external_dir.mkdir(parents=True, exist_ok=True)
    evil_csv = external_dir / "evil.csv"
    evil_csv.write_text("Data,Kwota\n2026-03-01,100\n", encoding="utf-8")

    return {
        "runtime_dir": runtime_dir,
        "uploads_dir": uploads_dir,
        "valid_csv": valid_csv,
        "evil_csv": evil_csv,
        "duckdb_file": duckdb_file,
        "reports_dir": reports_dir,
    }


def test_policy_runtime_disabled_by_default(isolated_env):
    settings = Settings(
        duckdb_path=str(isolated_env["duckdb_file"]),
        reports_dir=str(isolated_env["reports_dir"]),
        daas_policy_enforce=False,
    )
    runner = PipelineRunner(settings=settings)
    req = RunRequest(pipeline="ecommerce_demo", force_mock=True)
    result = runner.run(req)

    assert result.run_status in {RunStatus.SUCCESS, RunStatus.PARTIAL}
    audit_log = isolated_env["duckdb_file"].parent / "audit.jsonl"
    assert not audit_log.exists()


def test_policy_runtime_enforce_allows_valid_file(isolated_env, monkeypatch):
    # Ustaw WORKSPACE w policy na runtime_dir tego testu
    import app.core.policy as policy_mod
    monkeypatch.setattr(policy_mod, "WORKSPACE", isolated_env["runtime_dir"].resolve())

    settings = Settings(
        duckdb_path=str(isolated_env["duckdb_file"]),
        reports_dir=str(isolated_env["reports_dir"]),
        daas_policy_enforce=True,
    )
    runner = PipelineRunner(settings=settings)
    req = RunRequest(
        pipeline="ecommerce_demo",
        source_path=str(isolated_env["valid_csv"]),
    )
    result = runner.run(req)

    assert result.run_status == RunStatus.SUCCESS
    assert result.records_processed == 1

    audit_log = isolated_env["duckdb_file"].parent / "audit.jsonl"
    assert audit_log.exists()
    lines = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) >= 1
    assert lines[-1]["decision"] == "allow"
    assert lines[-1]["tool"] == "read_file"
    assert lines[-1]["role"] == "analyst"


def test_policy_runtime_enforce_blocks_path_traversal(isolated_env, monkeypatch):
    import app.core.policy as policy_mod
    monkeypatch.setattr(policy_mod, "WORKSPACE", isolated_env["runtime_dir"].resolve())

    settings = Settings(
        duckdb_path=str(isolated_env["duckdb_file"]),
        reports_dir=str(isolated_env["reports_dir"]),
        daas_policy_enforce=True,
    )
    runner = PipelineRunner(settings=settings)
    req = RunRequest(
        pipeline="ecommerce_demo",
        source_path=str(isolated_env["evil_csv"]),
    )
    result = runner.run(req)

    assert result.run_status == RunStatus.FAILED
    assert "PolicyError" in (result.error or "")
    # Komunikat odmowy nie ujawnia ukladu katalogow serwera
    assert str(isolated_env["runtime_dir"]) not in (result.error or "")
    assert str(isolated_env["evil_csv"]) not in (result.error or "")

    audit_log = isolated_env["duckdb_file"].parent / "audit.jsonl"
    assert audit_log.exists()
    lines = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) >= 1
    assert lines[-1]["decision"] == "deny:invalid_args"
    assert lines[-1]["tool"] == "read_file"


def test_policy_runtime_enforce_mock_without_source_path(isolated_env):
    """Flaga ON, brak source_path (tryb mock) -> policy nie jest wolane, brak audytu."""
    settings = Settings(
        duckdb_path=str(isolated_env["duckdb_file"]),
        reports_dir=str(isolated_env["reports_dir"]),
        daas_policy_enforce=True,
    )
    runner = PipelineRunner(settings=settings)
    result = runner.run(RunRequest(pipeline="ecommerce_demo", force_mock=True))

    assert result.run_status in {RunStatus.SUCCESS, RunStatus.PARTIAL}
    assert not (isolated_env["duckdb_file"].parent / "audit.jsonl").exists()


def test_policy_runtime_workspace_from_settings_no_monkeypatch(isolated_env):
    """Workspace pochodzi z Settings (runtime_dir tenanta), nie ze stalej modulu policy.

    Bez monkeypatchu WORKSPACE: plik w <reports_dir>/../uploads przechodzi,
    plik poza tym katalogiem jest odrzucany.
    """
    import app.core.policy as policy_mod

    settings = Settings(
        duckdb_path=str(isolated_env["duckdb_file"]),
        reports_dir=str(isolated_env["reports_dir"]),
        daas_policy_enforce=True,
    )
    # Stala modulu wskazuje na runtime/ projektu, NIE na tmp_path tego testu
    assert not isolated_env["runtime_dir"].resolve().is_relative_to(policy_mod.WORKSPACE)

    runner = PipelineRunner(settings=settings)
    ok = runner.run(RunRequest(pipeline="ecommerce_demo", source_path=str(isolated_env["valid_csv"])))
    assert ok.run_status == RunStatus.SUCCESS

    bad = runner.run(RunRequest(pipeline="ecommerce_demo", source_path=str(isolated_env["evil_csv"])))
    assert bad.run_status == RunStatus.FAILED
    assert "PolicyError" in (bad.error or "")

    lines = [json.loads(l) for l in (isolated_env["duckdb_file"].parent / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [l["decision"] for l in lines[-2:]] == ["allow", "deny:invalid_args"]
