"""Testy pipeline'u uksc_evidence: kontrakt paczki, integralnosc, storage, scoring, raport, diff, API."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.core.models import DataStatus, RunRequest, RunStatus, UkscPackage
from app.pipelines.runner import PIPELINES, PipelineRunner
from app.sources.uksc_source import UkscSource, compute_package_hash
from app.storage.duckdb_client import DuckDBClient
from app.transforms.uksc_transform import transform_uksc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "data" / "fixtures" / "uksc"
COLLECTOR = PROJECT_ROOT / "tools" / "uksc-collector.ps1"


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key in ("FACEIT_API_KEY", "APIFY_TOKEN", "DISCORD_WEBHOOK_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "test.duckdb"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("FIXTURES_DIR", str(PROJECT_ROOT / "data" / "fixtures"))
    reset_settings_cache()
    s = Settings(_env_file=None)
    s.ensure_dirs()
    (s.reports_path.parent / "uploads").mkdir(parents=True, exist_ok=True)
    yield s
    reset_settings_cache()


@pytest.fixture()
def runner(settings: Settings) -> PipelineRunner:
    return PipelineRunner(settings=settings, db=DuckDBClient(settings.duckdb_file))


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _put_upload(settings: Settings, raw: dict, name: str = "pkg.json") -> str:
    p = settings.reports_path.parent / "uploads" / name
    p.write_text(json.dumps(raw), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------- kontrakt
def test_fixtures_validate_and_hashes_consistent():
    for name in ("host_compliant.json", "host_noncompliant.json", "host_no_admin.json"):
        raw = _load(name)
        pkg = UkscPackage.model_validate(raw)
        assert pkg.schema_version == "1.0"
        for c in pkg.checks:
            canon = json.dumps(c.evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            assert c.evidence_hash == hashlib.sha256(canon).hexdigest(), c.control_id
        assert compute_package_hash([c.evidence_hash for c in pkg.checks]) == pkg.package_sha256


def test_package_rejects_unknown_fields_and_bad_schema_version():
    raw = _load("host_compliant.json")
    raw["checks"][0]["surprise"] = 1
    with pytest.raises(Exception):
        UkscPackage.model_validate(raw)
    raw = _load("host_compliant.json")
    raw["schema_version"] = "9.9"
    with pytest.raises(Exception):
        UkscPackage.model_validate(raw)


def test_schema_file_matches_model():
    on_disk = json.loads((PROJECT_ROOT / "schemas" / "uksc_collector_v1.json").read_text(encoding="utf-8"))
    fresh = UkscPackage.model_json_schema()
    assert on_disk["properties"] == fresh["properties"]
    assert on_disk["$defs"] == fresh["$defs"]


def test_collector_and_catalog_agree_on_control_ids():
    from tools.uksc_fixtures import CATALOG  # noqa: WPS433
    ps = COLLECTOR.read_text(encoding="ascii")  # ASCII-only file: read with strict codec = test
    ids_ps = set(re.findall(r"(?:Invoke-Check|Add-Check -Id) '([A-Z]{2,5}-\d{2})'", ps))
    ids_catalog = {c[0] for c in CATALOG}
    assert ids_ps == ids_catalog
    fixture_ids = {c["control_id"] for c in _load("host_compliant.json")["checks"]}
    assert fixture_ids == ids_catalog
    # kontrakt read-only: zadnych cmdletow mutujacych w collectorze
    for forbidden in ("Set-ItemProperty", "Remove-Item", "Stop-Process", "Set-Service", "Stop-Service", "Invoke-WebRequest", "Invoke-RestMethod"):
        assert forbidden not in ps, forbidden


# ---------------------------------------------------------------- source
def test_source_fixture_fallback_is_mock(settings: Settings):
    res = UkscSource(settings).fetch()
    assert res.data_status == DataStatus.MOCK_DATA
    assert len(res.records) == 32
    assert res.raw_meta["integrity"] == "ok"


def test_source_rejects_tampered_package(settings: Settings):
    raw = _load("host_compliant.json")
    raw["checks"][3]["evidence"]["tamper_protected"] = False  # zmiana dowodu bez przeliczenia hasha
    path = _put_upload(settings, raw)
    res = UkscSource(settings).fetch(source_path=path)
    assert res.data_status == DataStatus.BLOCKED_MISSING_SECRET
    assert "evidence_hash mismatch for INT-03" in res.detail
    assert res.records == []
    # podmiana calej kontroli z przeliczonym evidence_hash, ale bez package_sha256 -> tez odrzucone
    raw = _load("host_compliant.json")
    raw["checks"].pop()
    res = UkscSource(settings).fetch(source_path=_put_upload(settings, raw, "pkg2.json"))
    assert res.data_status == DataStatus.BLOCKED_MISSING_SECRET and "package_sha256" in res.detail


def test_source_rejects_path_outside_uploads(settings: Settings, tmp_path: Path):
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps(_load("host_compliant.json")), encoding="utf-8")
    res = UkscSource(settings).fetch(source_path=str(outside))
    assert res.data_status == DataStatus.BLOCKED_MISSING_SECRET
    assert "outside" in res.detail


def test_source_reports_validation_error_not_partial_import(settings: Settings):
    raw = _load("host_compliant.json")
    raw["checks"][0]["status"] = "MAYBE"
    res = UkscSource(settings).fetch(source_path=_put_upload(settings, raw))
    assert res.data_status == DataStatus.BLOCKED_MISSING_SECRET
    assert "schema validation" in res.detail and res.records == []


# ---------------------------------------------------------------- transform
def test_transform_scoring_noncompliant_and_no_admin():
    raw = _load("host_noncompliant.json")
    m = transform_uksc(raw["checks"], raw["host"], raw["collected_at_utc"])
    assert m.checks_total == 32 and m.manual == 6
    assert m.failed > 0 and m.warned > 0
    assert m.coverage_pct == round(100 * (m.passed + m.failed + m.warned) / 32, 1)
    refs = {a["uksc_ref"]: a for a in m.by_article}
    assert refs["art. 8 ust. 1 pkt 2 lit. k"]["verdict"] == "FAIL"        # BitLocker off
    assert refs["zal. 4 pkt 11"]["verdict"] == "MANUAL"

    raw = _load("host_no_admin.json")
    m2 = transform_uksc(raw["checks"], raw["host"], raw["collected_at_utc"])
    assert m2.na_no_admin > 0 and not m2.is_admin_run
    assert m2.coverage_pct < m.coverage_pct


# ---------------------------------------------------------------- pipeline end-to-end
def test_pipeline_registered_without_secrets(runner: PipelineRunner):
    assert "uksc_evidence" in PIPELINES
    info = {p.name: p for p in runner.list_pipelines()}["uksc_evidence"]
    assert info.required_secrets == [] and info.secrets_present is True


def test_pipeline_run_writes_db_and_report(runner: PipelineRunner):
    res = runner.run(RunRequest(pipeline="uksc_evidence", force_mock=True, client_name="Demo Sp. z o.o."))
    assert res.run_status == RunStatus.SUCCESS and res.data_status == DataStatus.MOCK_DATA
    assert res.records_processed == 32
    counts = runner.db.table_counts()
    assert counts["uksc_hosts"] == 1 and counts["uksc_checks"] == 32 and counts["uksc_inventory"] == 3
    md = Path(res.report_path).read_text(encoding="utf-8")
    assert "Dowod UKSC" in md and "art. 8 ust. 1 pkt 2 lit. k" in md
    assert "Demo Sp. z o.o." in md
    for banned in ("certyfikuje", "gwarantuje zgodnosc", "pelna zgodnosc"):
        assert banned not in md.lower()
    html = Path(res.report_html_path).read_text(encoding="utf-8")
    assert "<table" in html
    assert res.metrics["coverage_pct"] == 81.2


def test_pipeline_diff_vs_previous_run_same_host(runner: PipelineRunner, settings: Settings):
    raw_fail = _load("host_noncompliant.json")
    raw_ok = _load("host_compliant.json")
    raw_ok["host"] = raw_fail["host"]  # ten sam host: najpierw niezgodny, potem naprawiony
    from tools.uksc_fixtures import _pkg_hash  # noqa: WPS433
    raw_ok["package_sha256"] = _pkg_hash([c["evidence_hash"] for c in raw_ok["checks"]])

    r1 = runner.run(RunRequest(pipeline="uksc_evidence", source_path=_put_upload(settings, raw_fail, "a.json")))
    r2 = runner.run(RunRequest(pipeline="uksc_evidence", source_path=_put_upload(settings, raw_ok, "b.json")))
    assert r1.run_status == RunStatus.SUCCESS and r2.run_status == RunStatus.SUCCESS
    assert r1.data_status == DataStatus.LIVE_DATA
    md = Path(r2.report_path).read_text(encoding="utf-8")
    assert "Zmiany vs poprzedni run" in md
    assert "ENC-01" in md.split("Poprawione")[1].split("\n")[0]
    assert runner.db.previous_uksc_run(raw_fail["host"]["name"], r2.run_id) == r1.run_id


def test_pipeline_failed_run_when_package_bad(runner: PipelineRunner, settings: Settings):
    raw = _load("host_compliant.json")
    raw["package_sha256"] = "0" * 64
    res = runner.run(RunRequest(pipeline="uksc_evidence", source_path=_put_upload(settings, raw)))
    assert res.run_status == RunStatus.FAILED
    assert "package_sha256" in (res.error or "")


def test_api_accepts_uksc_pipeline(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    from app.main import app
    with TestClient(app) as client:
        r = client.get("/pipelines")
        assert r.status_code == 200
        assert any(p["name"] == "uksc_evidence" for p in r.json())
        r = client.post("/run", json={"pipeline": "uksc_evidence", "force_mock": True})
        assert r.status_code == 200, r.text
        assert r.json()["run_status"] == "SUCCESS"
