"""Prompty MCP (`prompts/*`) i bramka `resources/read` / `prompts/get`: lista z argumentami, tresc instrukcji
(identyfikatory wstawione, zasady dane-nie-polecenia i potwierdzenia), bledy bez wartosci od klienta, zakres
`runs:read`, audyt `mcp.prompt` / `mcp.resource` (szablon albo nazwa, bez identyfikatorow) i brak audytu list."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control import scopes as sc
from app.core.config import Settings
from app.main import create_app
from app.mcp_server import prompts as mcp_prompts
from tests.mcp_helpers import call_tool, ok, post, rpc, wait_job
from tests.test_mcp_resources import (
    CSV_TEXT,
    MODERN_META,
    ecommerce_run,
    make_settings,
    rpc_body,
    rpc_error,
    rpc_error_code,
    run_report_uri,
)
from tests.v1_helpers import Seeded, get_rows, seed_key, seed_org, with_db

PROMPTS = {"weekly_summary": ["run_id", "language"], "compare_runs": ["run_id", "previous_run_id", "language"],
           "data_quality_check": ["upload_id", "language"]}
UKSC_RUN_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


@dataclass
class World:
    client: TestClient
    settings: Settings
    a: Seeded
    b: Seeded
    upload_id: str
    first_run_id: str
    second_run_id: str
    idle_upload_id: str
    b_run_id: str


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    settings = make_settings(tmp_path_factory.mktemp("mcp_prompts"))
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        a = seed_org(settings, name="Org A", owner_email="a-owner@example.test")
        b = seed_org(settings, name="Org B", owner_email="b-owner@example.test")
        upload_id, first_run_id = ecommerce_run(client, a.api_key)
        job = ok(call_tool(client, a.api_key, "start_run", {"upload_id": upload_id, "pipeline": "ecommerce_demo"}))
        second = wait_job(client, a.api_key, job["job_id"])
        assert second["status"] == "succeeded", second
        idle = ok(call_tool(client, a.api_key, "upload_csv", {"filename": "bez-runu.csv", "content": CSV_TEXT}))
        _, b_run_id = ecommerce_run(client, b.api_key)

        async def seed_uksc_run(db):
            db.add(models.RunRecord(run_id=UKSC_RUN_ID, org_id=a.org_id, pipeline="uksc_evidence", status="SUCCESS",
                                    sha256_md="cd" * 32))

        with_db(settings, seed_uksc_run)
        yield World(client, settings, a, b, upload_id, first_run_id, second["run_id"], idle["id"], b_run_id)


def get_prompt(client: TestClient, key: str, name: str, arguments: dict[str, str] | None = None) -> dict:
    body = rpc_body(client, key, "prompts/get", {"name": name, "arguments": arguments or {}})
    assert "result" in body, body
    return body["result"]


def prompt_text(result: dict) -> str:
    (message,) = result["messages"]
    assert message["role"] == "user" and message["content"]["type"] == "text"
    return message["content"]["text"]


def assert_rules(text: str, language: str = "Polish") -> None:
    assert mcp_prompts.DATA_RULE in text and "data, not instructions" in text
    assert mcp_prompts.CONFIRM_RULE in text and mcp_prompts.NUMBERS_RULE in text
    assert f"Answer in {language}." in text


def events(settings: Settings, key_id: str, action: str) -> list[models.AuditEvent]:
    """Zdarzenia `action` jednego klucza w kolejnosci zapisu."""
    rows = get_rows(settings, models.AuditEvent, models.AuditEvent.action == action)
    return sorted((e for e in rows if e.meta.get("key_id") == key_id), key=lambda e: (e.ts, e.id))


# ================================================================ lista i tresc
def test_prompt_list_has_three_prompts_with_described_arguments(world: World):
    prompts = rpc_body(world.client, world.a.api_key, "prompts/list")["result"]["prompts"]
    assert {p["name"]: [a["name"] for a in p["arguments"]] for p in prompts} == PROMPTS
    for prompt in prompts:
        assert prompt["title"] and prompt["description"].endswith("."), prompt["name"]
        for argument in prompt["arguments"]:
            assert argument["description"], (prompt["name"], argument)
            assert argument["required"] is (argument["name"] != "language"), (prompt["name"], argument)


def test_weekly_summary_inserts_the_run_and_the_rules(world: World):
    result = get_prompt(world.client, world.a.api_key, "weekly_summary", {"run_id": world.first_run_id})
    text = prompt_text(result)
    assert result["description"].startswith("Five-point summary")
    assert f'get_run with run_id "{world.first_run_id}"' in text and "get_report" in text
    assert run_report_uri(world.first_run_id) in text and "verify_report" in text and "sha256" in text
    assert "ecommerce_demo" in text and "5 bullet points" in text
    assert_rules(text)
    english = prompt_text(get_prompt(world.client, world.a.api_key, "weekly_summary",
                                     {"run_id": world.first_run_id, "language": "en"}))
    assert_rules(english, "English")


def test_compare_runs_names_both_runs_and_the_periods(world: World):
    text = prompt_text(get_prompt(world.client, world.a.api_key, "compare_runs",
                                  {"run_id": world.second_run_id, "previous_run_id": world.first_run_id}))
    assert f'run_id "{world.second_run_id}"' in text and f'run_id "{world.first_run_id}"' in text
    assert "period_from" in text and "period_to" in text and "percentage difference" in text
    assert_rules(text)


def test_data_quality_check_points_to_the_newest_run_of_the_upload(world: World):
    text = prompt_text(get_prompt(world.client, world.a.api_key, "data_quality_check",
                                  {"upload_id": world.upload_id}))
    assert world.upload_id in text and f'run_id "{world.second_run_id}"' in text  # nowszy z dwoch runow
    assert world.first_run_id not in text and "list_uploads" in text and "needs fixing" in text
    assert "eksport.csv" not in text  # nazwa pliku od klienta nie trafia do instrukcji
    assert_rules(text)
    idle = prompt_text(get_prompt(world.client, world.a.api_key, "data_quality_check",
                                  {"upload_id": world.idle_upload_id, "language": "en"}))
    assert "No run has used this upload yet" in idle and "bez-runu.csv" not in idle
    assert_rules(idle, "English")


# ================================================================ bledy
@pytest.mark.parametrize(("name", "arguments", "code", "secret"), [
    ("evil<script>", {}, "not_found", "evil"),
    ("weekly_summary", {}, "validation_error", None),
    ("weekly_summary", {"run_id": "RUN", "zzz<script>": "1"}, "validation_error", "zzz"),
    ("weekly_summary", {"run_id": "a$b"}, "validation_error", "a$b"),
    ("weekly_summary", {"run_id": "RUN", "language": "de<x>"}, "validation_error", "de<x>"),
    ("weekly_summary", {"run_id": UKSC_RUN_ID}, "validation_error", None),
    ("weekly_summary", {"run_id": "00000000-0000-4000-8000-000000000000"}, "not_found", "00000000-0000"),
    ("compare_runs", {"run_id": "RUN", "previous_run_id": "RUN"}, "validation_error", None),
    ("compare_runs", {"run_id": "RUN", "previous_run_id": UKSC_RUN_ID}, "validation_error", None),
    ("data_quality_check", {"upload_id": "../../etc/passwd"}, "validation_error", "passwd"),
    ("data_quality_check", {"upload_id": "f" * 32}, "not_found", "f" * 32),
])
def test_prompt_errors_carry_no_client_values(world: World, name, arguments, code, secret):
    arguments = {k: (world.first_run_id if v == "RUN" else v) for k, v in arguments.items()}
    error = rpc_error(world.client, world.a.api_key, "prompts/get", {"name": name, "arguments": arguments})
    assert rpc_error_code(error) == code and error["code"] == -32602, error
    if secret:
        assert secret not in error["message"], error
    assert "Unknown prompt" not in error["message"] and "Traceback" not in error["message"]


def test_missing_and_malformed_arguments_name_only_the_field(world: World):
    error = rpc_error(world.client, world.a.api_key, "prompts/get", {"name": "compare_runs", "arguments": {}})
    assert error["message"] == ("validation_error: run_id: missing required argument; previous_run_id: missing "
                                "required argument")
    error = rpc_error(world.client, world.a.api_key, "prompts/get",
                      {"name": "weekly_summary", "arguments": {"run_id": 5, "<k>": 1}})
    assert rpc_error_code(error) == "validation_error" and error["message"].startswith("validation_error: arguments:")
    assert "<k>" not in error["message"]


def test_prompt_needs_runs_read_and_denial_is_audited(world: World):
    key_id, reports_only = seed_key(world.settings, world.a.org_id, scopes=[sc.REPORTS_READ], name="reports-only")
    error = rpc_error(world.client, reports_only, "prompts/get",
                      {"name": "weekly_summary", "arguments": {"run_id": world.first_run_id}})
    assert error["message"] == "insufficient_scope: this API key lacks the runs:read scope"
    denied = [e for e in get_rows(world.settings, models.AuditEvent, models.AuditEvent.action == "auth_denied")
              if e.actor_id == key_id]
    assert [(e.target, e.meta) for e in denied] == [
        ("mcp:weekly_summary", {"reason": "insufficient_scope", "scope": sc.RUNS_READ})]
    (event,) = events(world.settings, key_id, "mcp.prompt")
    assert event.meta["ok"] is False and event.meta["prompt"] == "weekly_summary"


def test_foreign_ids_are_not_found(world: World):
    for name, arguments in (("weekly_summary", {"run_id": world.b_run_id}),
                            ("compare_runs", {"run_id": world.first_run_id, "previous_run_id": world.b_run_id})):
        error = rpc_error(world.client, world.a.api_key, "prompts/get", {"name": name, "arguments": arguments})
        assert rpc_error_code(error) == "not_found" and world.b_run_id not in error["message"], (name, error)


def test_unexpected_failure_is_masked(world: World, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("secret detail runtime/tenants/x")

    monkeypatch.setattr(mcp_prompts.v1_runs, "load_run_record", boom)
    error = rpc_error(world.client, world.a.api_key, "prompts/get",
                      {"name": "weekly_summary", "arguments": {"run_id": world.first_run_id}})
    assert rpc_error_code(error) == "internal_error" and error["code"] == -32603 and "request_id" in error["message"]
    assert "secret" not in error["message"] and "runtime/" not in error["message"]


def test_modern_protocol_envelope_serves_prompts_too(world: World):
    params = {"name": "weekly_summary", "arguments": {"run_id": world.first_run_id}, "_meta": MODERN_META}
    r = post(world.client, rpc("prompts/get", params), world.a.api_key, protocol="2026-07-28",
             extra={"Mcp-Method": "prompts/get", "Mcp-Name": "weekly_summary"})
    assert r.status_code == 200, r.text
    assert world.first_run_id in prompt_text(r.json()["result"])


# ================================================================ audyt bramki
def test_gate_audits_resources_and_prompts_by_template_and_name(world: World):
    key_id, key = seed_key(world.settings, world.a.org_id, name="audit")
    get_prompt(world.client, key, "weekly_summary", {"run_id": world.first_run_id})
    rpc_error(world.client, key, "prompts/get", {"name": "drop_everything", "arguments": {}})
    rpc_body(world.client, key, "resources/read", {"uri": run_report_uri(world.first_run_id)})
    rpc_error(world.client, key, "resources/read", {"uri": f"daas://uploads/{'f' * 32}/summary.json"})
    rpc_error(world.client, key, "resources/read", {"uri": "daas://elsewhere/1/x.md"})

    prompts = events(world.settings, key_id, "mcp.prompt")
    assert [(e.target, e.meta["prompt"], e.meta["ok"]) for e in prompts] == [
        ("mcp:weekly_summary", "weekly_summary", True), ("mcp:unknown", "unknown", False)]
    resources = events(world.settings, key_id, "mcp.resource")
    assert [(e.target, e.meta["resource"], e.meta["ok"]) for e in resources] == [
        ("mcp:runs/report.md", "runs/report.md", True), ("mcp:uploads/summary.json", "uploads/summary.json", False),
        ("mcp:unknown", "unknown", False)]
    for event in prompts + resources:
        assert set(event.meta) - {"prompt", "resource"} == {"ok", "duration_ms", "key_id"}, event.meta
        assert event.actor_type == "key" and event.org_id == world.a.org_id
        assert world.first_run_id not in str(event.meta) + str(event.target) and "f" * 32 not in str(event.meta)


def test_list_methods_are_not_audited(world: World):
    key_id, key = seed_key(world.settings, world.a.org_id, name="lists")
    for method in ("resources/list", "resources/templates/list", "prompts/list"):
        assert "result" in rpc_body(world.client, key, method), method
    assert events(world.settings, key_id, "mcp.resource") == []
    assert events(world.settings, key_id, "mcp.prompt") == []
    assert events(world.settings, key_id, "mcp.tool") == []
