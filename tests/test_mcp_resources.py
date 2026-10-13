"""Zasoby MCP (`resources/*`): szablony, lista najnowszych raportow organizacji, odczyt (ta sama tresc i hash co
`get_report` / `get_evidence`), bledy (`not_found`, `insufficient_scope`, `report_expired`, `report_too_large`, URI spoza
szablonow bez odbijania wartosci) i tryb selfhost. Audyt `mcp.resource` i bramka bledow SDK: `test_mcp_prompts.py`;
izolacja najemcow: `test_mcp_isolation.py`. Pomocnicy `rpc_body`, `read_resource`, `resource_text`, `rpc_error`
i `rpc_error_code` sa uzywani tez w tamtych plikach."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control import scopes as sc
from app.control.types import utcnow
from app.core.config import Settings
from app.main import create_app
from app.mcp_server import resources as mcp_resources
from app.mcp_server import tools as mcp_tools
from tests.conftest import PROJECT_ROOT, TEST_API_KEY, TEST_SECRET_KEY
from tests.mcp_helpers import call_tool, ok, post, rpc, wait_job
from tests.test_runs import PACKAGE_PATH
from tests.v1_helpers import API, Seeded, get_rows, key_headers, seed_key, seed_org, with_db

CSV_TEXT = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_text(encoding="utf-8")
TEMPLATES = {
    "daas://runs/{run_id}/report.md": "text/markdown",
    "daas://runs/{run_id}/report.json": "application/json",
    "daas://runs/{run_id}/evidence.json": "application/json",
    "daas://uploads/{upload_id}/summary.json": "application/json",
}
MODERN_META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
               "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"},
               "io.modelcontextprotocol/clientCapabilities": {}}


# ================================================================ pomocnicy JSON-RPC
def rpc_body(client: TestClient, key: str, method: str, params: dict[str, Any] | None = None, **kwargs) -> dict:
    """Cala odpowiedz JSON-RPC (wynik albo blad); HTTP zawsze 200 w erze handshake."""
    r = post(client, rpc(method, params), key, **kwargs)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json"), r.headers["content-type"]
    return r.json()


def read_resource(client: TestClient, key: str, uri: str) -> dict:
    body = rpc_body(client, key, "resources/read", {"uri": uri})
    assert "result" in body, body
    return body["result"]


def resource_text(client: TestClient, key: str, uri: str) -> str:
    (content,) = read_resource(client, key, uri)["contents"]
    assert content["uri"] == uri
    return content["text"]


def rpc_error(client: TestClient, key: str, method: str, params: dict[str, Any] | None = None, **kwargs) -> dict:
    """Blad JSON-RPC metody (`resources/read`, `prompts/get`): bez `data` (SDK wstawia tam URI klienta)."""
    body = rpc_body(client, key, method, params, **kwargs)
    assert "error" in body and "result" not in body, body
    assert "data" not in body["error"], body["error"]
    return body["error"]


def rpc_error_code(error: dict) -> str:
    """Kod domenowy z tekstu `code: message`."""
    return error["message"].split(":", 1)[0]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_report_uri(run_id: str, name: str = "report.md") -> str:
    return f"daas://runs/{run_id}/{name}"


# ================================================================ swiat: dwie organizacje z prawdziwymi runami
def ecommerce_run(client: TestClient, key: str, filename: str = "eksport.csv") -> tuple[str, str]:
    """(upload_id, run_id) zakonczonego runu `ecommerce_demo` z pliku fixture."""
    up = ok(call_tool(client, key, "upload_csv", {"filename": filename, "content": CSV_TEXT}))
    job = ok(call_tool(client, key, "start_run", {"upload_id": up["id"], "pipeline": "ecommerce_demo"}))
    done = wait_job(client, key, job["job_id"])
    assert done["status"] == "succeeded", done
    return up["id"], done["run_id"]


def collector_run(client: TestClient, org: Seeded) -> str:
    """run_id zakonczonego runu `uksc_evidence` (paczka kolektora przez REST, start przez MCP)."""
    r = client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                    files={"file": ("paczka.json", PACKAGE_PATH.read_bytes(), "application/json")})
    assert r.status_code == 201, r.text
    job = ok(call_tool(client, org.api_key, "start_run", {"upload_id": r.json()["id"], "pipeline": "uksc_evidence"}))
    done = wait_job(client, org.api_key, job["job_id"])
    assert done["status"] == "succeeded", done
    return done["run_id"]


def make_settings(root: Path) -> Settings:
    settings = Settings(
        _env_file=None, daas_mode="cloud", daas_secret_key=TEST_SECRET_KEY, daas_api_key=None,
        daas_control_db_url=f"sqlite+aiosqlite:///{root / 'control.db'}",
        duckdb_path=str(root / "runtime" / "daas.duckdb"), reports_dir=str(root / "runtime" / "reports"),
        fixtures_dir=str(PROJECT_ROOT / "data" / "fixtures"),
        daas_rate_limit_ip_per_min=0, daas_metrics_token=None, daas_allow_console_mail=True)
    settings.ensure_dirs()
    return settings


@dataclass
class World:
    client: TestClient
    settings: Settings
    a: Seeded
    b: Seeded
    a_upload_id: str
    a_run_id: str
    a_uksc_run_id: str
    b_run_id: str


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    settings = make_settings(tmp_path_factory.mktemp("mcp_resources"))
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        a = seed_org(settings, name="Org A", owner_email="a-owner@example.test")
        b = seed_org(settings, name="Org B", owner_email="b-owner@example.test")
        a_upload_id, a_run_id = ecommerce_run(client, a.api_key)
        a_uksc_run_id = collector_run(client, a)
        _, b_run_id = ecommerce_run(client, b.api_key)
        yield World(client, settings, a, b, a_upload_id, a_run_id, a_uksc_run_id, b_run_id)


def scoped_key(w: World, scopes: list[str]) -> tuple[str, str]:
    return seed_key(w.settings, w.a.org_id, scopes=scopes, name="scoped")


# ================================================================ szablony i lista
def test_templates_are_listed_with_mime_types_and_the_untrusted_note(world: World):
    templates = rpc_body(world.client, world.a.api_key, "resources/templates/list")["result"]["resourceTemplates"]
    assert {t["uriTemplate"]: t["mimeType"] for t in templates} == TEMPLATES
    for template in templates:
        assert template["description"].endswith(mcp_tools.UNTRUSTED), template["name"]
        assert template["name"] and template["title"], template
    by_uri = {t["uriTemplate"]: t for t in templates}
    assert "200 000" in by_uri["daas://runs/{run_id}/report.md"]["description"]
    assert "2 MiB" in by_uri["daas://runs/{run_id}/report.json"]["description"]


def test_list_shows_the_reports_of_the_key_organization_only(world: World):
    listed = rpc_body(world.client, world.a.api_key, "resources/list")["result"]["resources"]
    assert {r["uri"] for r in listed} == {run_report_uri(world.a_run_id), run_report_uri(world.a_uksc_run_id)}
    assert listed[0]["uri"] == run_report_uri(world.a_uksc_run_id)  # najnowszy najpierw
    run = ok(call_tool(world.client, world.a.api_key, "get_run", {"run_id": world.a_run_id}))
    for item in listed:
        assert re.fullmatch(r"(ecommerce_demo|uksc_evidence) \d{4}-\d{2}-\d{2} (SUCCESS|PARTIAL|FAILED)",
                            item["name"]), item
        assert item["mimeType"] == "text/markdown" and item["description"].endswith(mcp_tools.UNTRUSTED)
        assert "text" not in item and "contents" not in item  # lista bez tresci
    ecommerce = next(item for item in listed if world.a_run_id in item["uri"])
    assert ecommerce["name"] == f"ecommerce_demo {run['created_at'][:10]} SUCCESS"
    assert run["report_sha256"]["md"] in ecommerce["description"]
    assert world.b_run_id not in json.dumps(listed)
    other = rpc_body(world.client, world.b.api_key, "resources/list")["result"]["resources"]
    assert [r["uri"] for r in other] == [run_report_uri(world.b_run_id)]


def test_list_holds_the_newest_reports_still_on_disk(world: World):
    c = seed_org(world.settings, name="Org C", owner_email="c-owner@example.test")
    now = utcnow()

    async def seed(db):
        for i in range(mcp_resources.LIST_LIMIT + 3):  # i = 0 to najnowszy
            db.add(models.RunRecord(
                run_id=f"00000000-0000-4000-8000-{i:012d}", org_id=c.org_id, pipeline="ecommerce_demo",
                status="SUCCESS", sha256_md=None if i == 1 else f"{i:064x}", created_at=now - timedelta(hours=i),
                report_purged_at=now if i == 0 else None))

    with_db(world.settings, seed)
    listed = rpc_body(world.client, c.api_key, "resources/list")["result"]["resources"]
    expected = [run_report_uri(f"00000000-0000-4000-8000-{i:012d}") for i in range(2, 2 + mcp_resources.LIST_LIMIT)]
    assert [r["uri"] for r in listed] == expected  # bez runu po retencji (0) i bez raportu Markdown (1)


def test_list_needs_both_run_and_report_scopes(world: World):
    for scopes in ([sc.RUNS_READ], [sc.REPORTS_READ], [sc.USAGE_READ]):
        _, key = scoped_key(world, scopes)
        assert rpc_body(world.client, key, "resources/list")["result"]["resources"] == [], scopes
    _, key = scoped_key(world, [sc.RUNS_READ, sc.REPORTS_READ])
    assert len(rpc_body(world.client, key, "resources/list")["result"]["resources"]) == 2


# ================================================================ odczyt
def test_markdown_resource_equals_get_report(world: World):
    uri = run_report_uri(world.a_run_id)
    (content,) = read_resource(world.client, world.a.api_key, uri)["contents"]
    report = ok(call_tool(world.client, world.a.api_key, "get_report", {"run_id": world.a_run_id}))
    assert content == {"uri": uri, "mimeType": "text/markdown", "text": report["content"]}
    assert report["truncated"] is False and sha256_text(content["text"]) == report["sha256"]


def test_json_resource_is_the_full_json_report(world: World):
    text = resource_text(world.client, world.a.api_key, run_report_uri(world.a_run_id, "report.json"))
    report = ok(call_tool(world.client, world.a.api_key, "get_report", {"run_id": world.a_run_id, "format": "json"}))
    assert json.loads(text)["run_id"] == world.a_run_id
    assert text == report["content"] and sha256_text(text) == report["sha256"]


def test_long_markdown_is_cut_like_get_report_and_json_never(world: World, monkeypatch):
    monkeypatch.setattr(mcp_tools, "MAX_REPORT_CHARS", 50)
    text = resource_text(world.client, world.a.api_key, run_report_uri(world.a_run_id))
    report = ok(call_tool(world.client, world.a.api_key, "get_report", {"run_id": world.a_run_id}))
    assert report["truncated"] is True and text == report["content"] and "[truncated:" in text
    full = resource_text(world.client, world.a.api_key, run_report_uri(world.a_run_id, "report.json"))
    assert len(full) > 50 and json.loads(full)  # JSON nie jest obcinany (uciety by sie nie parsowal)


def test_json_over_the_limit_is_report_too_large(world: World, monkeypatch):
    monkeypatch.setattr(mcp_resources, "MAX_JSON_BYTES", 100)
    error = rpc_error(world.client, world.a.api_key, "resources/read",
                      {"uri": run_report_uri(world.a_run_id, "report.json")})
    assert rpc_error_code(error) == "report_too_large" and error["code"] == -32602, error


def test_evidence_resource_of_a_collector_run(world: World):
    text = resource_text(world.client, world.a.api_key, run_report_uri(world.a_uksc_run_id, "evidence.json"))
    evidence = ok(call_tool(world.client, world.a.api_key, "get_evidence", {"run_id": world.a_uksc_run_id}))
    assert json.loads(text) == evidence and evidence["checks"] and evidence["package_sha256"]
    error = rpc_error(world.client, world.a.api_key, "resources/read",
                      {"uri": run_report_uri(world.a_run_id, "evidence.json")})
    assert rpc_error_code(error) == "not_found", error  # run ecommerce_demo nie ma statusu artefaktow


def test_upload_summary_is_metadata_without_content(world: World):
    uri = f"daas://uploads/{world.a_upload_id}/summary.json"
    (content,) = read_resource(world.client, world.a.api_key, uri)["contents"]
    assert content["mimeType"] == "application/json"
    summary = json.loads(content["text"])
    item = next(u for u in ok(call_tool(world.client, world.a.api_key, "list_uploads"))["items"]
                if u["id"] == world.a_upload_id)
    assert summary == {k: item[k] for k in ("id", "kind", "filename", "bytes", "sha256", "created_at",
                                            "suggested_pipeline")}
    assert CSV_TEXT.splitlines()[1] not in content["text"]


def test_foreign_and_unknown_ids_are_not_found(world: World):
    for uri in (run_report_uri(world.b_run_id), run_report_uri(world.b_run_id, "report.json"),
                run_report_uri("00000000-0000-4000-8000-000000000000"),
                f"daas://uploads/{'f' * 32}/summary.json"):
        error = rpc_error(world.client, world.a.api_key, "resources/read", {"uri": uri})
        assert rpc_error_code(error) == "not_found", (uri, error)
        assert world.b_run_id not in error["message"] and "f" * 32 not in error["message"]


def test_missing_scope_is_insufficient_scope_and_audited(world: World):
    key_id, runs_only = scoped_key(world, [sc.RUNS_READ])
    error = rpc_error(world.client, runs_only, "resources/read", {"uri": run_report_uri(world.a_run_id)})
    assert error["message"] == "insufficient_scope: this API key lacks the reports:read scope"
    _, reports_only = scoped_key(world, [sc.REPORTS_READ])
    error = rpc_error(world.client, reports_only, "resources/read",
                      {"uri": f"daas://uploads/{world.a_upload_id}/summary.json"})
    assert rpc_error_code(error) == "insufficient_scope" and "runs:read" in error["message"]
    denied = [e for e in get_rows(world.settings, models.AuditEvent, models.AuditEvent.org_id == world.a.org_id,
                                  models.AuditEvent.action == "auth_denied") if e.actor_id == key_id]
    assert [(e.target, e.meta) for e in denied] == [
        ("mcp:runs/report.md", {"reason": "insufficient_scope", "scope": sc.REPORTS_READ})]


def test_expired_report_is_report_expired(world: World):
    run_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"

    async def seed(db):
        db.add(models.RunRecord(run_id=run_id, org_id=world.a.org_id, pipeline="ecommerce_demo", status="SUCCESS",
                                sha256_md="ab" * 32, sha256_json="cd" * 32, report_purged_at=utcnow()))

    with_db(world.settings, seed)
    for name in ("report.md", "report.json"):
        error = rpc_error(world.client, world.a.api_key, "resources/read", {"uri": run_report_uri(run_id, name)})
        assert rpc_error_code(error) == "report_expired", error
    listed = rpc_body(world.client, world.a.api_key, "resources/list")["result"]["resources"]
    assert run_id not in json.dumps(listed)  # lista pokazuje tylko raporty, ktore da sie przeczytac


@pytest.mark.parametrize(("params", "code", "secret"), [
    ({"uri": "daas://runs/a$b/report.md"}, "validation_error", "a$b"),
    ({"uri": "daas://runs/%2E%2E/report.md"}, "validation_error", "%2E"),
    ({"uri": "daas://uploads/NOT-HEX-ID/summary.json"}, "validation_error", "NOT-HEX"),
    ({"uri": "daas://runs/x/report.html"}, "not_found", "report.html"),
    ({"uri": "https://evil.example.test/<script>"}, "not_found", "evil"),
    ({"uri": 5}, "validation_error", None),
    ({}, "validation_error", None),
])
def test_uris_outside_the_templates_are_rejected_without_echo(world: World, params, code, secret):
    error = rpc_error(world.client, world.a.api_key, "resources/read", params)
    assert rpc_error_code(error) == code and error["code"] == -32602, error
    if secret:
        assert secret not in error["message"]
    assert "Traceback" not in error["message"] and "Unknown resource" not in error["message"]


def test_unexpected_failure_is_masked(world: World, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("secret detail runtime/tenants/x/reports")

    monkeypatch.setattr(mcp_resources.v1_runs, "read_report_file", boom)
    error = rpc_error(world.client, world.a.api_key, "resources/read",
                      {"uri": run_report_uri(world.a_run_id, "report.json")})
    assert rpc_error_code(error) == "internal_error" and error["code"] == -32603 and "request_id" in error["message"]
    assert "secret" not in error["message"] and "runtime/" not in error["message"]


def test_modern_protocol_envelope_reads_resources_too(world: World):
    uri = run_report_uri(world.a_run_id, "report.json")
    r = post(world.client, rpc("resources/read", {"uri": uri, "_meta": MODERN_META}), world.a.api_key,
             protocol="2026-07-28", extra={"Mcp-Method": "resources/read", "Mcp-Name": uri})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["contents"][0]["text"] == resource_text(world.client, world.a.api_key, uri)
    r = post(world.client, rpc("resources/list", {"_meta": MODERN_META}), world.a.api_key, protocol="2026-07-28",
             extra={"Mcp-Method": "resources/list"})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["cacheScope"] == "private" and len(result["resources"]) == 2  # wynik per organizacja: nie wspoldzielic
    bogus = "daas://runs/x/report.html"
    r = post(world.client, rpc("resources/read", {"uri": bogus, "_meta": MODERN_META}), world.a.api_key,
             protocol="2026-07-28", extra={"Mcp-Method": "resources/read", "Mcp-Name": bogus})
    assert r.json()["error"]["message"] == "not_found: unknown resource", r.text


# ================================================================ selfhost
def test_selfhost_deployment_key_reads_resources(anon_client: TestClient):
    _, run_id = ecommerce_run(anon_client, TEST_API_KEY)
    assert [r["uri"] for r in rpc_body(anon_client, TEST_API_KEY, "resources/list")["result"]["resources"]] == [
        run_report_uri(run_id)]
    text = resource_text(anon_client, TEST_API_KEY, run_report_uri(run_id))
    assert text == ok(call_tool(anon_client, TEST_API_KEY, "get_report", {"run_id": run_id}))["content"]
