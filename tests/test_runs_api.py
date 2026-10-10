"""API v1 runow i raportow: /evidence, paginacja, ETag/304, integralnosc, 404/410, sciezki wzgledne (T1-T3, C11, C12)."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import runs as runs_module
from app.control import models
from app.control.tenancy import TenantPaths
from app.control.types import utcnow
from app.core.config import Settings
from tests.conftest import PROJECT_ROOT
from tests.test_runs import PACKAGE_PATH, run_pipeline
from tests.v1_helpers import API, key_headers, seed_org, update_rows

CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def url(org, suffix: str) -> str:
    return f"{API}/orgs/{org.org_id}{suffix}"


def report_url(org, run_id: str) -> str:
    return url(org, f"/runs/{run_id}/report")


def hdr(org) -> dict[str, str]:
    return key_headers(org.api_key)


def finished(client: TestClient, org) -> dict:
    """Run e-commerce z raportami; zwraca `{run_id, sha, files}`."""
    job = run_pipeline(client, org, "ecommerce_demo", filename="dane.csv", data=CSV_BYTES)
    run_id = job["run_id"]
    detail = client.get(url(org, f"/runs/{run_id}"), headers=hdr(org)).json()
    return {"run_id": run_id, "sha": detail["report_sha256"], "job": job}


def report_files(settings: Settings, org, run_id: str) -> dict[str, Path]:
    reports = TenantPaths.for_org(settings, org.org_id).reports
    short = run_id[:8]
    return {p.suffix[1:]: p for p in reports.iterdir() if short in p.name and "latest" not in p.name}


# ================================================================ T1: /evidence
def test_evidence_is_404_before_any_collector_run(cloud_client, org):
    r = cloud_client.get(url(org, "/evidence"), headers=hdr(org))
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_evidence_ignores_runs_of_other_pipelines(cloud_client, org):
    finished(cloud_client, org)
    assert cloud_client.get(url(org, "/evidence"), headers=hdr(org)).status_code == 404


def test_evidence_after_collector_run(cloud_client, org):
    job = run_pipeline(cloud_client, org, "uksc_evidence", filename="paczka.json", data=PACKAGE_PATH.read_bytes())
    r = cloud_client.get(url(org, "/evidence"), headers=hdr(org))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] == job["run_id"] and body["host"] and body["package_sha256"]
    assert body["checks"], body
    first = body["checks"][0]
    assert {"control_id", "requirement", "title", "status", "artifact"} <= set(first)
    assert len(first["artifact"]["sha256"]) == 64


def test_evidence_reports_the_run_status(cloud_client, cloud_settings, org):
    job = run_pipeline(cloud_client, org, "uksc_evidence", filename="paczka.json", data=PACKAGE_PATH.read_bytes())
    assert cloud_client.get(url(org, "/evidence"), headers=hdr(org)).json()["run_status"] == "SUCCESS"
    update_rows(cloud_settings, models.RunRecord, {"status": "PARTIAL"}, models.RunRecord.run_id == job["run_id"])
    body = cloud_client.get(url(org, "/evidence"), headers=hdr(org)).json()
    assert body["run_id"] == job["run_id"] and body["run_status"] == "PARTIAL"   # niepelny run jest widoczny jako taki


def test_evidence_is_scoped_to_the_organization(cloud_client, cloud_settings, org):
    run_pipeline(cloud_client, org, "uksc_evidence", filename="paczka.json", data=PACKAGE_PATH.read_bytes())
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    assert cloud_client.get(url(other, "/evidence"), headers=hdr(other)).status_code == 404


# ================================================================ T2: paginacja
def test_runs_pagination_with_pipeline_filter_and_cursor(cloud_client, org):
    cs2 = [run_pipeline(cloud_client, org, "cs2_demo", force_mock=True)["run_id"] for _ in range(3)]
    other = finished(cloud_client, org)["run_id"]

    first = cloud_client.get(url(org, "/runs"), params={"pipeline": "cs2_demo", "limit": 2}, headers=hdr(org)).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = cloud_client.get(url(org, "/runs"), params={"pipeline": "cs2_demo", "limit": 2,
                                                         "cursor": first["next_cursor"]}, headers=hdr(org)).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    seen = [i["run_id"] for i in first["items"] + second["items"]]
    assert sorted(seen) == sorted(cs2) and len(set(seen)) == 3 and other not in seen
    assert all(i["pipeline"] == "cs2_demo" for i in first["items"] + second["items"])
    # najnowsze pierwsze
    created = [i["created_at"] for i in first["items"] + second["items"]]
    assert created == sorted(created, reverse=True)


def test_runs_without_filter_returns_every_pipeline_and_no_cursor_on_last_page(cloud_client, org):
    run_pipeline(cloud_client, org, "cs2_demo", force_mock=True)
    finished(cloud_client, org)
    page = cloud_client.get(url(org, "/runs"), params={"limit": 5}, headers=hdr(org)).json()
    assert {i["pipeline"] for i in page["items"]} == {"cs2_demo", "ecommerce_demo"} and page["next_cursor"] is None


@pytest.mark.parametrize("cursor", ["nie-data|x", "|", "2026-13-45T00:00:00|x"])
def test_invalid_cursor_is_422(cloud_client, org, cursor):
    r = cloud_client.get(url(org, "/runs"), params={"cursor": cursor}, headers=hdr(org))
    assert r.status_code == 422 and r.json()["code"] == "invalid_cursor"


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_bounds(cloud_client, org, limit):
    assert cloud_client.get(url(org, "/runs"), params={"limit": limit}, headers=hdr(org)).status_code == 422


# ================================================================ T3: ETag / If-None-Match
@pytest.fixture()
def run(cloud_client, org):
    return finished(cloud_client, org)


def test_report_has_etag_equal_to_recorded_hash(cloud_client, org, run):
    for fmt in ("html", "md", "json"):
        r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": fmt}, headers=hdr(org))
        assert r.status_code == 200 and r.headers["etag"] == f'"{run["sha"][fmt]}"'
        assert r.headers["cache-control"] == "private, no-store"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in r.headers["content-security-policy"]


def _inm(sha: str, style: str) -> str:
    return {
        "strong": f'"{sha}"',
        "weak": f'W/"{sha}"',
        "list": f'"deadbeef", "{sha}"',
        "weak_list": f'W/"deadbeef" , W/"{sha}"',
        "star": "*",
        "bare": sha,
    }[style]


@pytest.mark.parametrize("style", ["strong", "weak", "list", "weak_list", "star", "bare"])
def test_if_none_match_gives_304(cloud_client, org, run, style):
    sha = run["sha"]["md"]
    r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": "md"},
                         headers={**hdr(org), "If-None-Match": _inm(sha, style)})
    assert r.status_code == 304 and r.content == b""
    assert r.headers["etag"] == f'"{sha}"'


@pytest.mark.parametrize("header", ['"0000"', 'W/"0000"', '"a", "b"', ""])
def test_non_matching_if_none_match_gives_200(cloud_client, org, run, header):
    r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": "md"},
                         headers={**hdr(org), "If-None-Match": header})
    assert r.status_code == 200 and r.content


def test_etag_of_another_format_does_not_match(cloud_client, org, run):
    r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": "md"},
                         headers={**hdr(org), "If-None-Match": f'"{run["sha"]["html"]}"'})
    assert r.status_code == 200


# ================================================================ integralnosc, 404, 410
def test_changed_file_is_a_500_integrity_error(cloud_client, cloud_settings, org, run):
    path = report_files(cloud_settings, org, run["run_id"])["md"]
    path.write_bytes(path.read_bytes() + b"\nmanipulacja")
    r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": "md"}, headers=hdr(org))
    assert r.status_code == 500 and r.json()["code"] == "report_integrity_error"
    assert "manipulacja" not in r.text and str(path) not in r.text
    # 304 tez nie omija kontroli integralnosci
    r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": "md"},
                         headers={**hdr(org), "If-None-Match": f'"{run["sha"]["md"]}"'})
    assert r.status_code == 500


def test_missing_file_is_404_report_file(cloud_client, cloud_settings, org, run):
    report_files(cloud_settings, org, run["run_id"])["html"].unlink()
    r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": "html"}, headers=hdr(org))
    assert r.status_code == 404 and r.json()["code"] == "not_found" and "report file" in r.json()["detail"]


def test_unknown_run_and_unrecorded_format_are_404(cloud_client, cloud_settings, org, run):
    assert cloud_client.get(report_url(org, "no-such-run"), headers=hdr(org)).status_code == 404
    update_rows(cloud_settings, models.RunRecord, {"sha256_json": None}, models.RunRecord.run_id == run["run_id"])
    r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": "json"}, headers=hdr(org))
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_purged_report_is_410_report_expired(cloud_client, cloud_settings, org, run):
    update_rows(cloud_settings, models.RunRecord, {"report_purged_at": utcnow() - timedelta(days=1)},
                models.RunRecord.run_id == run["run_id"])
    for fmt in ("html", "md", "json"):
        r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": fmt}, headers=hdr(org))
        assert r.status_code == 410 and r.json()["code"] == "report_expired", fmt
    # rekord i hashe zostaja: szczegoly runu i verify dzialaja dalej
    assert cloud_client.get(url(org, f"/runs/{run['run_id']}"), headers=hdr(org)).status_code == 200
    v = cloud_client.post(url(org, "/verify"), json={"sha256": run["sha"]["md"]}, headers=hdr(org))
    assert v.json()["known"] is True


def test_report_purge_flag_is_read_tolerantly(monkeypatch):
    """Dopoki kolumny nie ma w modelu (starsze schematy), brak atrybutu = raport nie jest wygasly."""
    class Legacy:
        pass

    assert runs_module._purged(Legacy()) is False


# ================================================================ C12: hash z wczytanych bajtow
def test_hash_is_computed_from_the_bytes_that_are_served(cloud_client, cloud_settings, org, run, monkeypatch):
    path = report_files(cloud_settings, org, run["run_id"])["md"]
    real = Path.read_bytes
    reads: list[Path] = []

    def counting(self: Path) -> bytes:
        if self == path or self.resolve() == path.resolve():
            reads.append(self)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", counting)

    def forbidden(*a, **k):
        raise AssertionError("sha256_file nie moze byc uzyte do raportu (drugi odczyt pliku)")

    monkeypatch.setattr(runs_module, "sha256_file", forbidden, raising=False)
    r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": "md"}, headers=hdr(org))
    assert r.status_code == 200 and len(reads) == 1
    assert r.content == real(path)


# ================================================================ C11: sciezki wzgledne / przeniesiony runtime
def _rewrite_paths(client: TestClient, org, run_id: str, prefix: str) -> None:
    db = client.app.state.tenants.get(org.org_id).db
    with db.connect() as con:
        con.execute(
            "UPDATE runs SET report_path = ? || regexp_extract(report_path, '[^/]+$'), "
            "report_json_path = ? || regexp_extract(report_json_path, '[^/]+$'), "
            "report_html_path = ? || regexp_extract(report_html_path, '[^/]+$') WHERE run_id = ?",
            [prefix, prefix, prefix, run_id])


def test_report_is_served_after_the_runtime_directory_moved(cloud_client, org, run):
    _rewrite_paths(cloud_client, org, run["run_id"], "/srv/old-host/runtime/tenants/xyz/reports/")
    for fmt in ("html", "md", "json"):
        r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": fmt}, headers=hdr(org))
        assert r.status_code == 200, fmt
        assert r.headers["etag"] == f'"{run["sha"][fmt]}"'


def test_moved_runtime_with_windows_style_stored_path(cloud_client, org, run):
    _rewrite_paths(cloud_client, org, run["run_id"], "C:\\\\daas\\\\runtime\\\\")
    db = cloud_client.app.state.tenants.get(org.org_id).db
    with db.connect() as con:
        con.execute("UPDATE runs SET report_path = replace(report_path, '/', '\\\\') WHERE run_id = ?", [run["run_id"]])
    r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": "md"}, headers=hdr(org))
    assert r.status_code == 200


def test_stored_path_to_a_foreign_file_is_never_served(cloud_client, cloud_settings, org, run, tmp_path):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    secret = foreign / "secret-not-a-report.md"
    secret.write_text("tajne", encoding="utf-8")
    db = cloud_client.app.state.tenants.get(org.org_id).db
    with db.connect() as con:
        con.execute("UPDATE runs SET report_path = ? WHERE run_id = ?", [str(secret), run["run_id"]])
    r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": "md"}, headers=hdr(org))
    assert r.status_code == 404 and "tajne" not in r.text


def test_traversal_in_stored_path_is_not_followed(cloud_client, cloud_settings, org, run):
    db = cloud_client.app.state.tenants.get(org.org_id).db
    with db.connect() as con:
        con.execute("UPDATE runs SET report_path = ? WHERE run_id = ?", ["../../../../etc/passwd", run["run_id"]])
    r = cloud_client.get(report_url(org, run["run_id"]), params={"fmt": "md"}, headers=hdr(org))
    assert r.status_code == 404
