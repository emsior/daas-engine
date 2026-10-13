"""Izolacja najemcow na `/mcp`: dwie organizacje x KAZDE narzedzie.

Klucz organizacji A wola kazde narzedzie z identyfikatorami zasobow organizacji B (job, run, upload, hash raportu).
Konwencja: identyfikator obcej organizacji = `not_found` (bez ujawniania istnienia); narzedzia listujace i opisujace
organizacje zwracaja wylacznie dane A. Zadna odpowiedz nie zawiera identyfikatorow B. Nowe narzedzie bez przypadku
w `CASES` zawali `test_matrix_covers_every_tool`. To samo dla zasobow (`resources/read` kazdego szablonu,
`resources/list`) i promptow (`prompts/get`): `RESOURCE_CASES`, `PROMPT_CASES`, `test_matrix_covers_every_resource_and_prompt`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.core.config import Settings
from app.main import create_app
from app.mcp_server import prompts as mcp_prompts
from app.mcp_server import resources as mcp_resources
from tests.conftest import PROJECT_ROOT, TEST_SECRET_KEY
from tests.mcp_helpers import TOOL_NAMES, call_tool, error_code, ok, text_of, wait_job
from tests.test_mcp_resources import rpc_body, rpc_error, rpc_error_code
from tests.v1_helpers import Seeded, get_rows, seed_org, with_db

CSV_TEXT = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_text(encoding="utf-8")
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


# ================================================================ zasoby i prompty
#: (szablon zasobu, URI z identyfikatorem B): klucz A zawsze dostaje `not_found`.
RESOURCE_CASES: list[tuple[str, Callable[[World], str]]] = [
    ("runs/report.md", lambda w: f"daas://runs/{w.b_run_id}/report.md"),
    ("runs/report.json", lambda w: f"daas://runs/{w.b_run_id}/report.json"),
    ("runs/evidence.json", lambda w: f"daas://runs/{UKSC_RUN_ID}/evidence.json"),
    ("uploads/summary.json", lambda w: f"daas://uploads/{w.b_upload_id}/summary.json"),
]
#: (prompt, argumenty z identyfikatorami B): klucz A zawsze dostaje `not_found`.
PROMPT_CASES: list[tuple[str, Callable[[World], dict[str, str]]]] = [
    ("weekly_summary", lambda w: {"run_id": w.b_run_id}),
    ("compare_runs", lambda w: {"run_id": w.b_run_id, "previous_run_id": UKSC_RUN_ID}),
    ("data_quality_check", lambda w: {"upload_id": w.b_upload_id}),
]


def test_matrix_covers_every_resource_and_prompt():
    assert sorted(name for name, _ in RESOURCE_CASES) == sorted(spec.shape for spec in mcp_resources.SPECS)
    assert sorted(name for name, _ in PROMPT_CASES) == sorted(spec.name for spec in mcp_prompts.SPECS)


def _assert_nothing_of_b(world: World, payload: Any, label: str) -> None:
    serialized = repr(payload)
    for secret in world.b_secrets:
        assert secret not in serialized, (label, secret)


@pytest.mark.parametrize(("template", "uri"), RESOURCE_CASES, ids=[c[0] for c in RESOURCE_CASES])
def test_key_of_a_never_reads_resources_of_b(world: World, template, uri):
    error = rpc_error(world.client, world.a.api_key, "resources/read", {"uri": uri(world)})
    assert rpc_error_code(error) == "not_found", error
    _assert_nothing_of_b(world, error, template)


@pytest.mark.parametrize(("prompt", "arguments"), PROMPT_CASES, ids=[c[0] for c in PROMPT_CASES])
def test_key_of_a_never_gets_prompts_about_resources_of_b(world: World, prompt, arguments):
    error = rpc_error(world.client, world.a.api_key, "prompts/get", {"name": prompt, "arguments": arguments(world)})
    assert rpc_error_code(error) == "not_found", error
    _assert_nothing_of_b(world, error, prompt)


def test_resource_list_of_a_shows_nothing_of_b(world: World):
    listed = rpc_body(world.client, world.a.api_key, "resources/list")["result"]["resources"]
    _assert_nothing_of_b(world, listed, "resources/list")
    # kontrola pozytywna: te same identyfikatory dzialaja kluczem B
    own = {r["uri"] for r in rpc_body(world.client, world.b.api_key, "resources/list")["result"]["resources"]}
    assert f"daas://runs/{world.b_run_id}/report.md" in own
    for template, uri in RESOURCE_CASES:
        if template != "runs/evidence.json":  # run uksc B jest tylko rekordem bez danych najemcy
            assert "result" in rpc_body(world.client, world.b.api_key, "resources/read", {"uri": uri(world)}), template
    for prompt, arguments in PROMPT_CASES:
        if prompt != "compare_runs":  # B: run ecommerce vs run uksc = rozne pipeline'y (validation_error)
            body = rpc_body(world.client, world.b.api_key, "prompts/get",
                            {"name": prompt, "arguments": arguments(world)})
            assert "result" in body, (prompt, body)


def test_resource_and_prompt_audit_stays_per_org(world: World):
    rpc_error(world.client, world.a.api_key, "resources/read", {"uri": f"daas://runs/{world.b_run_id}/report.md"})
    rpc_error(world.client, world.a.api_key, "prompts/get",
              {"name": "weekly_summary", "arguments": {"run_id": world.b_run_id}})
    events_a = get_rows(world.settings, models.AuditEvent, models.AuditEvent.org_id == world.a.org_id,
                        models.AuditEvent.action.in_(("mcp.resource", "mcp.prompt")))
    assert {e.action for e in events_a} == {"mcp.resource", "mcp.prompt"}
    for event in events_a:
        assert event.meta["key_id"] == world.a.key_id and event.meta["ok"] is False
        _assert_nothing_of_b(world, (event.target, event.meta), event.action)
