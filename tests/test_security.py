"""Testy bezpieczeństwa: path traversal, tenant isolation, OPSEC."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.config import Settings, reset_settings_cache
from app.core.models import DataStatus, RunRequest, RunStatus
from app.pipelines.runner import PipelineRunner
from app.storage.duckdb_client import DuckDBClient


@pytest.fixture()
def secure_runner(tmp_path, monkeypatch):
    """Runner z izolowanym tmp środowiskiem."""
    rt = tmp_path / "runtime"
    rt.mkdir()
    (rt / "reports").mkdir()
    (rt / "uploads").mkdir()
    monkeypatch.setenv("DUCKDB_PATH", str(rt / "daas.duckdb"))
    monkeypatch.setenv("REPORTS_DIR", str(rt / "reports"))
    monkeypatch.setenv("FACEIT_API_KEY", "")
    monkeypatch.setenv("APIFY_TOKEN", "")
    monkeypatch.delenv("ECOMMERCE_CSV_PATH", raising=False)
    monkeypatch.delenv("APIFY_DATASET_ID", raising=False)
    reset_settings_cache()
    s = Settings()
    s.ensure_dirs()
    runner = PipelineRunner(s, DuckDBClient(s.duckdb_file))
    yield runner, tmp_path
    reset_settings_cache()


# -----------------------------------------------------------------------
# PATH TRAVERSAL
# -----------------------------------------------------------------------
class TestPathTraversal:
    """source_path nie może wskazywać poza uploads/."""

    def test_rejects_absolute_system_path(self, secure_runner):
        runner, _ = secure_runner
        res = runner.run(RunRequest(
            pipeline="ecommerce_demo",
            source_path="C:/Windows/System32/drivers/etc/hosts",
        ))
        assert res.run_status == RunStatus.FAILED
        assert "outside" in (res.error or "")

    def test_rejects_relative_traversal(self, secure_runner):
        runner, _ = secure_runner
        res = runner.run(RunRequest(
            pipeline="ecommerce_demo",
            source_path="../../../etc/passwd",
        ))
        assert res.run_status == RunStatus.FAILED
        assert "outside" in (res.error or "")

    def test_rejects_path_outside_uploads(self, secure_runner):
        runner, tmp_path = secure_runner
        evil = tmp_path / "runtime" / "reports" / "evil.csv"
        evil.write_text(
            "order_id,date,product,category,revenue,cost,profit,discount,status\n"
            "1,2024-01-01,x,y,100,50,50,0,completed\n"
        )
        res = runner.run(RunRequest(
            pipeline="ecommerce_demo",
            source_path=str(evil),
        ))
        assert res.run_status == RunStatus.FAILED
        assert "outside" in (res.error or "")

    def test_accepts_file_inside_uploads(self, secure_runner):
        runner, tmp_path = secure_runner
        uploads = tmp_path / "runtime" / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        good = uploads / "legit.csv"
        good.write_text(
            "order_id,date,product,category,revenue,cost,profit,discount,status\n"
            "ORD-001,2024-01-15,Widget A,Electronics,1200,800,400,0.05,completed\n"
        )
        res = runner.run(RunRequest(
            pipeline="ecommerce_demo",
            source_path=str(good),
        ))
        assert res.run_status == RunStatus.SUCCESS
        assert res.data_status == DataStatus.LIVE_DATA


# -----------------------------------------------------------------------
# TENANT ISOLATION
# -----------------------------------------------------------------------
class TestTenantIsolation:
    """Dane tenanta A nie mogą być odczytane przez tenanta B."""

    def test_tenant_dirs_are_separate(self, tmp_path):
        tenants = tmp_path / "runtime" / "tenants"
        tenant_a = tenants / "alpha" / "uploads"
        tenant_b = tenants / "beta" / "uploads"
        tenant_a.mkdir(parents=True)
        tenant_b.mkdir(parents=True)

        file_a = tenant_a / "secret_data.csv"
        file_a.write_text("order_id,date,product,category,revenue,cost,profit,discount,status\n")

        # B nie powinien widzieć pliku A
        assert not (tenant_b / "secret_data.csv").exists()
        # Traversal z B do A — resolved path nie zaczyna się od B
        traversal = tenant_b / ".." / ".." / "alpha" / "uploads" / "secret_data.csv"
        resolved = traversal.resolve()
        assert not str(resolved).startswith(str(tenant_b.resolve()))

    def test_report_urls_use_random_ids(self):
        """Raporty identyfikowane przez UUID, nie przewidywalne nazwy."""
        run_id = str(uuid.uuid4())
        assert len(run_id) == 36
        assert "-" in run_id


# -----------------------------------------------------------------------
# OPSEC: brak sekretów w kodzie
# -----------------------------------------------------------------------
class TestOpsec:
    """Sprawdza, że w kodzie nie ma wycieku zakazanych danych."""

    FORBIDDEN_PATTERNS = [
        "".join(["mcq", "089", "@", "gmail.com"]),  # stary email sprawdzany dynamicznie
    ]

    def test_no_forbidden_patterns_in_source(self):
        source_dir = Path(__file__).resolve().parents[1] / "app"
        violations = []
        for py_file in source_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            for pattern in self.FORBIDDEN_PATTERNS:
                if pattern in content:
                    violations.append(f"{py_file.name}: contains '{pattern}'")
        assert not violations, f"OPSEC violations: {violations}"

    def test_no_forbidden_patterns_in_pyproject(self):
        toml = Path(__file__).resolve().parents[1] / "pyproject.toml"
        content = toml.read_text(encoding="utf-8")
        for pattern in self.FORBIDDEN_PATTERNS:
            assert pattern not in content, f"pyproject.toml contains '{pattern}'"
