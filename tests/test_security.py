"""Testy bezpieczeństwa: path traversal, tenant isolation, OPSEC."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.core.models import DataStatus, RunRequest, RunStatus
from app.pipelines.runner import PipelineRunner
from app.storage.duckdb_client import DuckDBClient
from tests.conftest import PROJECT_ROOT


@pytest.fixture(params=[False, True], ids=["policy-off", "policy-on"])
def secure_runner(tmp_path, monkeypatch, request):
    """Runner z izolowanym tmp środowiskiem.

    Dwa warianty: policy wylaczone (straznik sciezki w zrodle — jedyna bariera, jak w v1.0) i policy
    wlaczone (domyslne od 2.0.0 — odmowa wczesniej, jako PolicyError). Obie bariery musza trzymac.
    """
    monkeypatch.setenv("DAAS_POLICY_ENFORCE", "true" if request.param else "false")
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


def _denied(runner: PipelineRunner, error: str | None) -> bool:
    """Policy-off: odmowa ze strazniki zrodla ("outside allowed uploads..."). Policy-on: PolicyError (katalog
    roboczy = runtime/) albo, dla sciezek w runtime/ poza uploads/, nadal straznik zrodla (obrona warstwowa)."""
    err = error or ""
    if runner.settings.daas_policy_enforce:
        return "PolicyError" in err or "outside" in err
    return "outside" in err


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
        assert _denied(runner, res.error)

    def test_rejects_relative_traversal(self, secure_runner):
        runner, _ = secure_runner
        res = runner.run(RunRequest(
            pipeline="ecommerce_demo",
            source_path="../../../etc/passwd",
        ))
        assert res.run_status == RunStatus.FAILED
        assert _denied(runner, res.error)

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
        assert _denied(runner, res.error)

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

    def test_rejects_sibling_prefix_directory(self, secure_runner):
        """Zabezpieczenie przed prefix collision: katalog 'uploads-evil' obok 'uploads'."""
        runner, tmp_path = secure_runner
        sibling_evil = tmp_path / "runtime" / "uploads-evil"
        sibling_evil.mkdir(parents=True, exist_ok=True)
        payload = sibling_evil / "payload.csv"
        payload.write_text(
            "order_id,date,product,category,revenue,cost,profit,discount,status\n"
            "ORD-999,2024-01-15,Evil Widget,Electronics,1200,800,400,0.05,completed\n"
        )
        res = runner.run(RunRequest(
            pipeline="ecommerce_demo",
            source_path=str(payload),
        ))
        assert res.run_status == RunStatus.FAILED
        assert _denied(runner, res.error)


# -----------------------------------------------------------------------
# TENANT ISOLATION (na endpointach)
# -----------------------------------------------------------------------
# Model E1: jeden klient = jedno wdrozenie (wlasny klucz, baza, uploads, raporty, audyt). Izolacja
# jest granica wdrozenia; testujemy ja przez HTTP na dwoch niezaleznych aplikacjach, nie na tmp_path.
KEY_A = "klucz-wdrozenia-A-0123456789"
KEY_B = "klucz-wdrozenia-B-9876543210"
CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()


@pytest.fixture()
def two_deployments(tmp_path):
    from contextlib import ExitStack

    from app.main import create_app

    def make(name: str, key: str) -> Settings:
        base = tmp_path / name
        s = Settings(_env_file=None, daas_api_key=key, duckdb_path=str(base / "daas.duckdb"),
                     reports_dir=str(base / "reports"), fixtures_dir=str(PROJECT_ROOT / "data" / "fixtures"))
        s.ensure_dirs()
        return s

    sa, sb = make("a", KEY_A), make("b", KEY_B)
    with ExitStack() as stack:
        ca = stack.enter_context(TestClient(create_app(sa), headers={"X-API-Key": KEY_A}))
        cb = stack.enter_context(TestClient(create_app(sb), headers={"X-API-Key": KEY_B}))
        yield ca, cb, sa, sb


class TestTenantIsolation:
    """Dane wdrozenia A nie sa dostepne dla wdrozenia B ani przez klucz, ani przez identyfikatory, ani przez sciezki."""

    def test_key_of_one_deployment_does_not_open_the_other(self, two_deployments):
        ca, cb, _, _ = two_deployments
        assert ca.get("/pipelines").status_code == 200
        assert cb.get("/pipelines").status_code == 200
        assert cb.get("/pipelines", headers={"X-API-Key": KEY_A}).status_code == 401
        assert ca.get("/pipelines", headers={"X-API-Key": KEY_B}).status_code == 401

    def test_runs_and_reports_are_not_shared(self, two_deployments):
        ca, cb, _, _ = two_deployments
        run_id = ca.post("/run", json={"pipeline": "cs2_demo"}).json()["run_id"]
        assert ca.get(f"/runs/{run_id}").status_code == 200
        assert cb.get("/runs").json() == []
        assert cb.get(f"/runs/{run_id}").status_code == 404
        assert cb.get(f"/reports/run/{run_id}").status_code == 404
        assert cb.get("/reports/cs2_demo/latest").status_code == 404

    def test_upload_id_of_one_deployment_is_unknown_to_the_other(self, two_deployments):
        ca, cb, _, _ = two_deployments
        up = ca.post("/upload", files={"file": ("dane.csv", CSV_BYTES, "text/csv")}).json()
        assert ca.post("/run", json=up["run_payload"]).json()["run_status"] == "SUCCESS"
        r = cb.post("/run", json=up["run_payload"])
        assert r.status_code == 404 and r.json()["detail"] == "upload not found"

    def test_source_path_into_another_deployment_is_blocked(self, two_deployments):
        ca, cb, sa, _ = two_deployments
        ca.post("/upload", files={"file": ("dane.csv", CSV_BYTES, "text/csv")})
        foreign = next((sa.reports_path.parent / "uploads").glob("*.csv"))
        r = cb.post("/run", json={"pipeline": "ecommerce_demo", "source_path": str(foreign)})
        body = r.json()
        assert body["run_status"] == "FAILED" and body["error"].startswith("POLICY_DENIED")
        assert str(foreign) not in r.text

    @pytest.mark.parametrize("ident", ["..%2f..%2fetc%2fpasswd", "%2e%2e%2f%2e%2e%2fsecret", "..\\..\\x"])
    def test_traversal_in_identifiers_never_reaches_the_filesystem(self, two_deployments, ident):
        _, cb, _, _ = two_deployments
        assert cb.get(f"/reports/run/{ident}").status_code == 404
        assert cb.get(f"/runs/{ident}").status_code == 404

    def test_audit_trails_are_separate(self, two_deployments):
        ca, cb, sa, sb = two_deployments
        ca.post("/run", json={"pipeline": "cs2_demo"})
        cb.get("/pipelines", headers={"X-API-Key": KEY_A})  # odmowa na B
        a_events = [json.loads(x)["action"] for x in sa.audit_log_path.read_text(encoding="utf-8").splitlines()]
        b_events = [json.loads(x)["action"] for x in sb.audit_log_path.read_text(encoding="utf-8").splitlines()]
        assert a_events == ["run"]
        assert b_events == ["auth_denied"]

    def test_run_ids_are_unguessable_uuids(self, two_deployments):
        ca, _, _, _ = two_deployments
        run_id = ca.post("/run", json={"pipeline": "cs2_demo"}).json()["run_id"]
        assert str(uuid.UUID(run_id)) == run_id and uuid.UUID(run_id).version == 4


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
