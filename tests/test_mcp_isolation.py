"""Izolacja najemcow na `/mcp`: dwie organizacje x KAZDE narzedzie.

Klucz organizacji A wola kazde narzedzie z identyfikatorami zasobow organizacji B (job, run, upload, hash raportu).
Konwencja: identyfikator obcej organizacji = `not_found` (bez ujawniania istnienia); narzedzia listujace i opisujace
organizacje zwracaja wylacznie dane A. Zadna odpowiedz nie zawiera identyfikatorow B. Nowe narzedzie bez przypadku
w `CASES` zawali `test_matrix_covers_every_tool`.
"""
from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.core.config import Settings
from app.main import create_app
from tests.conftest import PROJECT_ROOT, TEST_SECRET_KEY
from tests.mcp_helpers import TOOL_NAMES, call_tool, error_code, ok, text_of, wait_job
from tests.test_xlsx_upload import fixture_workbook, workbook_bytes
from tests.v1_helpers import Seeded, get_rows, seed_org, with_db

CSV_TEXT = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_text(encoding="utf-8")
XLSX_B64 = base64.b64encode(workbook_bytes(fixture_workbook())).decode("ascii")
UKSC_RUN_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@dataclass
class World:
    client: TestClient
    settings: Settings
    a: Seeded
    b: Seeded
    b_upload_id: str
    b_job_id: str
    b_run_id: str
    b_sha_md: str

    @property
    def b_secrets(self) -> tuple[str, ...]:
        return (self.b.org_id, self.b_upload_id, self.b_job_id, self.b_run_id, self.b_sha_md, UKSC_RUN_ID, "Org B")


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    root: Path = tmp_path_factory.mktemp("mcp_isolation")
    settings = Settings(
        _env_file=None, daas_mode="cloud", daas_secret_key=TEST_SECRET_KEY, daas_api_key=None,
        daas_control_db_url=f"sqlite+aiosqlite:///{root / 'control.db'}",
        duckdb_path=str(root / "runtime" / "daas.duckdb"), reports_dir=str(root / "runtime" / "reports"),
        fixtures_dir=str(PROJECT_ROOT / "data" / "fixtures"),
        daas_rate_limit_ip_per_min=0, daas_metrics_token=None, daas_allow_console_mail=True)
    settings.ensure_dirs()
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        a = seed_org(settings, name="Org A", owner_email="a-owner@example.test")
        b = seed_org(settings, name="Org B", owner_email="b-owner@example.test")
        up = ok(call_tool(client, b.api_key, "upload_csv", {"filename": "b.csv", "content": CSV_TEXT}))
        job = ok(call_tool(client, b.api_key, "start_run", {"upload_id": up["id"], "pipeline": "ecommerce_demo"}))
        done = wait_job(client, b.api_key, job["job_id"])
        assert done["status"] == "succeeded", done
        run = ok(call_tool(client, b.api_key, "get_run", {"run_id": done["run_id"]}))

        async def seed_uksc_run(db):
            db.add(models.RunRecord(run_id=UKSC_RUN_ID, org_id=b.org_id, pipeline="uksc_evidence", status="SUCCESS",
                                    sha256_md="cd" * 32))

        with_db(settings, seed_uksc_run)
        yield World(client, settings, a, b, up["id"], job["job_id"], done["run_id"], run["report_sha256"]["md"])


#: (narzedzie, argumenty z identyfikatorami B, oczekiwanie): `not_found` albo funkcja sprawdzajaca wynik udany.
Check = Callable[[World, dict[str, Any]], None]


def _only_a(w: World, data: dict[str, Any]) -> None:
    assert data.get("org_id", w.a.org_id) == w.a.org_id


def _empty(w: World, data: dict[str, Any]) -> None:
    assert data["items"] == [], data


def _a_upload_only(w: World, data: dict[str, Any]) -> None:
    assert data["id"] != w.b_upload_id


def _no_upload_of_b(w: World, data: dict[str, Any]) -> None:
    assert all(item["id"] != w.b_upload_id for item in data["items"]), data


CASES: list[tuple[str, Callable[[World], dict[str, Any]], str | Check]] = [
    ("whoami", lambda w: {}, _only_a),
    ("list_pipelines", lambda w: {}, lambda w, d: None),
    ("list_uploads", lambda w: {"limit": 100}, _no_upload_of_b),
    ("upload_csv", lambda w: {"filename": "a.csv", "content": CSV_TEXT}, _a_upload_only),
    ("upload_xlsx", lambda w: {"filename": "a.xlsx", "content_base64": XLSX_B64}, _a_upload_only),
    ("start_run", lambda w: {"upload_id": w.b_upload_id, "pipeline": "ecommerce_demo"}, "not_found"),
    ("get_job", lambda w: {"job_id": w.b_job_id}, "not_found"),
    ("list_runs", lambda w: {"limit": 100}, _empty),
    ("get_run", lambda w: {"run_id": w.b_run_id}, "not_found"),
    ("get_report", lambda w: {"run_id": w.b_run_id, "format": "json"}, "not_found"),
    ("verify_report", lambda w: {"run_id": w.b_run_id, "sha256": w.b_sha_md}, "not_found"),
    ("get_evidence", lambda w: {"run_id": UKSC_RUN_ID}, "not_found"),
    ("get_usage", lambda w: {}, _only_a),
]


def test_matrix_covers_every_tool():
    assert sorted(name for name, _, _ in CASES) == sorted(TOOL_NAMES)


@pytest.mark.parametrize(("tool", "args", "expect"), CASES, ids=[c[0] for c in CASES])
def test_key_of_a_never_reaches_resources_of_b(world: World, tool, args, expect):
    result = call_tool(world.client, world.a.api_key, tool, args(world))
    if expect == "not_found":
        assert error_code(result) == "not_found", text_of(result)
    else:
        expect(world, ok(result))
    serialized = repr(result)
    for secret in world.b_secrets:
        assert secret not in serialized, (tool, secret)


@pytest.mark.parametrize("period", [{"period_from": "2026-08-01", "period_to": "2026-08-07"},
                                    {"period_to": "2026-08-07"}], ids=["week", "open-start"])
def test_start_run_with_a_period_on_an_upload_of_b_is_not_found(world: World, period: dict[str, str]):
    # okres nie zmienia konwencji: upload B pod kluczem A wyglada jak nieistniejacy (i nie powstaje zadanie)
    result = call_tool(world.client, world.a.api_key, "start_run",
                       {"upload_id": world.b_upload_id, "pipeline": "ecommerce_demo", **period})
    assert error_code(result) == "not_found", text_of(result)
    for secret in world.b_secrets:
        assert secret not in repr(result), secret
    assert get_rows(world.settings, models.Job, models.Job.org_id == world.a.org_id) == []


def test_second_report_tool_of_b_is_hidden_too(world: World):
    # ten sam run B pod kluczem A: kazdy format raportu i weryfikacja dowolnym hashem
    for fmt in ("markdown", "json"):
        assert error_code(call_tool(world.client, world.a.api_key, "get_report",
                                    {"run_id": world.b_run_id, "format": fmt})) == "not_found"
    assert error_code(call_tool(world.client, world.a.api_key, "verify_report",
                                {"run_id": world.b_run_id, "sha256": "0" * 64})) == "not_found"


def test_positive_control_b_sees_its_own_resources(world: World):
    key = world.b.api_key
    assert ok(call_tool(world.client, key, "get_job", {"job_id": world.b_job_id}))["run_id"] == world.b_run_id
    assert ok(call_tool(world.client, key, "verify_report",
                        {"run_id": world.b_run_id, "sha256": world.b_sha_md}))["match"] is True
    assert [u["id"] for u in ok(call_tool(world.client, key, "list_uploads"))["items"]] == [world.b_upload_id]
    assert ok(call_tool(world.client, key, "whoami"))["org_id"] == world.b.org_id


def test_nothing_of_b_changed_and_audit_stays_per_org(world: World):
    uploads_b = get_rows(world.settings, models.Upload, models.Upload.org_id == world.b.org_id)
    jobs_b = get_rows(world.settings, models.Job, models.Job.org_id == world.b.org_id)
    assert [u.id for u in uploads_b] == [world.b_upload_id] and [j.id for j in jobs_b] == [world.b_job_id]
    events_a = get_rows(world.settings, models.AuditEvent, models.AuditEvent.org_id == world.a.org_id,
                        models.AuditEvent.action == "mcp.tool")
    assert events_a and all(e.meta["key_id"] == world.a.key_id for e in events_a)
