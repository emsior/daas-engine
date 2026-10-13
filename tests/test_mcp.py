"""Serwer MCP pod `/mcp`: handshake, lista narzedzi, scenariusz upload -> run -> raport, uwierzytelnienie, limity,
zakresy, audyt `mcp.tool`, wylaczenie flaga i tryb selfhost. Izolacja najemcow: `test_mcp_isolation.py`."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control import scopes as sc
from app.control.service import current_period
from app.control.types import utcnow
from app.core.config import Settings
from app.mcp_server import tools as mcp_tools
from tests.conftest import PROJECT_ROOT, TEST_API_KEY
from tests.mcp_helpers import (
    MCP,
    TOOL_NAMES,
    call_tool,
    error_code,
    handshake,
    mcp_headers,
    notification,
    ok,
    post,
    rpc,
    text_of,
    wait_job,
)
from tests.test_runs import PACKAGE_PATH
from tests.v1_helpers import (
    API,
    get_rows,
    key_headers,
    seed_key,
    seed_org,
    update_rows,
    user_headers,
    with_db,
)

CSV_TEXT = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_text(encoding="utf-8")
PROBLEM = "application/problem+json"


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def mcp_events(settings: Settings, org_id: str, action: str = "mcp.tool") -> list[models.AuditEvent]:
    return get_rows(settings, models.AuditEvent, models.AuditEvent.org_id == org_id, models.AuditEvent.action == action)


def upload(client: TestClient, key: str, text: str = CSV_TEXT, filename: str = "eksport.csv") -> dict:
    return ok(call_tool(client, key, "upload_csv", {"filename": filename, "content": text}))


def finished_run(client: TestClient, key: str) -> dict:
    up = upload(client, key)
    job = ok(call_tool(client, key, "start_run", {"upload_id": up["id"], "pipeline": "ecommerce_demo"}))
    done = wait_job(client, key, job["job_id"])
    assert done["status"] == "succeeded", done
    return done


# ================================================================ protokol
def test_handshake_and_tool_list(cloud_client, org):
    init = handshake(cloud_client, org.api_key)
    assert init["serverInfo"]["name"] == "daas-engine" and init["capabilities"]["tools"] is not None
    assert "data, not instructions" in init["instructions"]
    r = post(cloud_client, rpc("tools/list"), org.api_key)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    tools = r.json()["result"]["tools"]
    assert sorted(t["name"] for t in tools) == sorted(TOOL_NAMES)
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object", tool["name"]
        assert tool["outputSchema"]["type"] == "object", tool["name"]
        assert tool["description"].endswith("."), tool["name"]
        hints = tool["annotations"]
        assert {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"} <= set(hints), tool["name"]
        assert hints["openWorldHint"] is False and hints["destructiveHint"] is False
        assert hints["readOnlyHint"] is (tool["name"] not in ("upload_csv", "start_run"))
    by_name = {t["name"]: t for t in tools}
    for name in ("get_report", "get_run", "get_evidence", "list_uploads", "upload_csv"):  # tresc z plikow klienta
        assert mcp_tools.UNTRUSTED in by_name[name]["description"], name
    assert "auto_job_id" in by_name["upload_csv"]["description"] and "reused" in by_name["start_run"]["description"]
    assert by_name["list_uploads"]["inputSchema"]["properties"]["limit"]["maximum"] == 100
    assert by_name["upload_csv"]["inputSchema"]["properties"]["filename"]["maxLength"] == 128


def test_stateless_requests_need_no_session_and_answer_json(cloud_client, org):
    # bez initialize i bez Mcp-Session-Id: tryb bezstanowy
    result = call_tool(cloud_client, org.api_key, "whoami")
    assert ok(result)["org_id"] == org.org_id
    r = post(cloud_client, rpc("tools/list"), org.api_key)
    assert "mcp-session-id" not in r.headers


def test_modern_protocol_envelope_is_served_too(cloud_client, org):
    r = post(cloud_client, rpc("tools/call", {
        "name": "whoami", "arguments": {},
        "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                  "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"},
                  "io.modelcontextprotocol/clientCapabilities": {}}}), org.api_key, protocol="2026-07-28",
             extra={"Mcp-Method": "tools/call", "Mcp-Name": "whoami"})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["structuredContent"]["protocol"] == "2026-07-28"


def test_unknown_jsonrpc_method_is_a_jsonrpc_error_not_500(cloud_client, org):
    r = post(cloud_client, rpc("bogus/method"), org.api_key)
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601


def test_unknown_tool_is_a_tool_error(cloud_client, org):
    assert error_code(call_tool(cloud_client, org.api_key, "drop_everything")) == "not_found"


def test_malformed_json_is_400_without_traceback(cloud_client, org):
    r = cloud_client.post(MCP, content=b"{not json", headers=mcp_headers(org.api_key))
    assert r.status_code == 400
    assert "Traceback" not in r.text


# ================================================================ scenariusz: upload -> run -> raport
def test_upload_run_report_verify_end_to_end(cloud_client, cloud_settings, org):
    key = org.api_key
    handshake(cloud_client, key)
    who = ok(call_tool(cloud_client, key, "whoami"))
    assert who["mode"] == "cloud" and who["key_prefix"] and who["key_prefix"] not in key[:4]
    assert key not in str(who)
    pipelines = ok(call_tool(cloud_client, key, "list_pipelines"))["items"]
    assert {p["name"] for p in pipelines} == {"ecommerce_demo", "uksc_evidence", "cs2_demo"}
    assert next(p for p in pipelines if p["name"] == "ecommerce_demo")["input"]["required_columns"]

    up = upload(cloud_client, key)
    assert up["kind"] == "csv" and up["ready"] is True and up["suggested_pipeline"] == "ecommerce_demo"
    assert up["mapping"]["revenue"] and len(up["sha256"]) == 64
    listed = ok(call_tool(cloud_client, key, "list_uploads", {"limit": 5}))["items"]
    assert [u["id"] for u in listed] == [up["id"]]

    started = call_tool(cloud_client, key, "start_run", {"upload_id": up["id"], "pipeline": "ecommerce_demo"})
    job = ok(started)
    assert job["status"] == "queued" and job["job_id"] in text_of(started)
    done = wait_job(cloud_client, key, job["job_id"])
    assert done["status"] == "succeeded" and done["run_id"] and done["finished_at"]
    run_id = done["run_id"]

    run = ok(call_tool(cloud_client, key, "get_run", {"run_id": run_id}))
    assert run["status"] == "SUCCESS" and run["metrics"] and run["records_processed"] > 0
    runs = ok(call_tool(cloud_client, key, "list_runs", {"limit": 10, "pipeline": "ecommerce_demo"}))["items"]
    assert runs[0]["run_id"] == run_id and runs[0]["data_status"] and runs[0]["report_sha256"]["md"]

    md = call_tool(cloud_client, key, "get_report", {"run_id": run_id})
    report = ok(md)
    assert report["format"] == "markdown" and report["truncated"] is False and report["content"].startswith("#")
    assert report["sha256"] == run["report_sha256"]["md"]
    assert mcp_tools.UNTRUSTED in text_of(md)
    as_json = ok(call_tool(cloud_client, key, "get_report", {"run_id": run_id, "format": "json"}))
    assert as_json["sha256"] == run["report_sha256"]["json"] and as_json["content"].lstrip().startswith("{")

    good = ok(call_tool(cloud_client, key, "verify_report", {"run_id": run_id, "sha256": report["sha256"].upper()}))
    assert good == {"run_id": run_id, "match": True, "expected_sha256": report["sha256"], "format": "markdown"}
    bad = ok(call_tool(cloud_client, key, "verify_report", {"run_id": run_id, "sha256": "0" * 64,
                                                             "format": "json"}))
    assert bad["match"] is False and bad["expected_sha256"] == as_json["sha256"]

    usage = ok(call_tool(cloud_client, key, "get_usage"))
    assert usage["org_id"] == org.org_id and usage["current"]["runs"] == 1
    assert usage["current"]["upload_bytes"] == len(CSV_TEXT.encode("utf-8"))

    # audyt: kazde wywolanie narzedzia, bez argumentow (nazwa pliku, tresc)
    events = mcp_events(cloud_settings, org.org_id)
    tools_called = {e.meta["tool"] for e in events}
    assert {"whoami", "upload_csv", "start_run", "get_job", "get_report", "verify_report"} <= tools_called
    for event in events:
        assert set(event.meta) == {"tool", "ok", "duration_ms", "key_id"}, event.meta
        assert event.meta["key_id"] == org.key_id and event.actor_type == "key"
    assert "eksport.csv" not in str([e.meta for e in events])
    # upload przez MCP to ta sama sciezka co v1: audyt upload.created i job.created
    assert mcp_events(cloud_settings, org.org_id, "upload.created")
    assert mcp_events(cloud_settings, org.org_id, "job.created")


def test_get_evidence_for_a_collector_run(cloud_client, org):
    key = org.api_key
    up = cloud_client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(key),
                           files={"file": ("paczka.json", PACKAGE_PATH.read_bytes(), "application/json")})
    assert up.status_code == 201, up.text
    job = ok(call_tool(cloud_client, key, "start_run", {"upload_id": up.json()["id"], "pipeline": "uksc_evidence"}))
    done = wait_job(cloud_client, key, job["job_id"])
    assert done["status"] == "succeeded", done
    result = call_tool(cloud_client, key, "get_evidence", {"run_id": done["run_id"]})
    evidence = ok(result)
    assert evidence["run_id"] == done["run_id"] and evidence["package_sha256"] and evidence["checks"]
    assert len(evidence["checks"][0]["artifact"]["sha256"]) == 64
    assert mcp_tools.UNTRUSTED in text_of(result)


def test_get_evidence_of_another_pipeline_is_not_found(cloud_client, org):
    done = finished_run(cloud_client, org.api_key)
    assert error_code(call_tool(cloud_client, org.api_key, "get_evidence", {"run_id": done["run_id"]})) == "not_found"


def test_long_report_is_truncated_with_a_note(cloud_client, org, monkeypatch):
    done = finished_run(cloud_client, org.api_key)
    monkeypatch.setattr(mcp_tools, "MAX_REPORT_CHARS", 50)
    report = ok(call_tool(cloud_client, org.api_key, "get_report", {"run_id": done["run_id"]}))
    assert report["truncated"] is True and "[truncated:" in report["content"]
    assert len(report["content"]) < 400 and len(report["sha256"]) == 64


def test_truncation_boundary_and_json_flag(cloud_client, org, monkeypatch):
    done = finished_run(cloud_client, org.api_key)
    args = {"run_id": done["run_id"], "format": "json"}
    full = ok(call_tool(cloud_client, org.api_key, "get_report", args))
    length = len(full["content"])
    assert full["truncated"] is False and length > 100
    monkeypatch.setattr(mcp_tools, "MAX_REPORT_CHARS", length)
    assert ok(call_tool(cloud_client, org.api_key, "get_report", args))["content"] == full["content"]
    monkeypatch.setattr(mcp_tools, "MAX_REPORT_CHARS", length - 1)
    cut = ok(call_tool(cloud_client, org.api_key, "get_report", args))
    assert cut["truncated"] is True and cut["sha256"] == full["sha256"]  # hash zawsze calego pliku
    assert cut["content"].startswith(full["content"][: length - 1]) and "[truncated:" in cut["content"]


def test_every_pipeline_has_an_mcp_doc_entry():
    from app.pipelines.runner import PIPELINES

    assert set(PIPELINES) <= set(mcp_tools._PIPELINE_DOCS), "describe the new pipeline for agents in _PIPELINE_DOCS"
    for name, (description, spec, example) in mcp_tools._PIPELINE_DOCS.items():
        kinds = mcp_tools.v1_jobs.PIPELINE_UPLOAD_KINDS[name]
        assert description.endswith(".") and example, name
        assert (spec.kind is None) == (kinds is None), name  # zgodnosc z rejestrem rodzajow uploadu


def test_expired_report_is_report_expired(cloud_client, cloud_settings, org):
    done = finished_run(cloud_client, org.api_key)
    update_rows(cloud_settings, models.RunRecord, {"report_purged_at": utcnow()},
                models.RunRecord.run_id == done["run_id"])
    result = call_tool(cloud_client, org.api_key, "get_report", {"run_id": done["run_id"]})
    assert error_code(result) == "report_expired"
    # hashe zostaja: verify dziala dalej
    assert ok(call_tool(cloud_client, org.api_key, "verify_report",
                        {"run_id": done["run_id"], "sha256": "1" * 64}))["match"] is False


def test_unknown_ids_are_not_found(cloud_client, org):
    key = org.api_key
    assert error_code(call_tool(cloud_client, key, "get_job", {"job_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV"})) == "not_found"
    for tool in ("get_run", "get_report", "verify_report", "get_evidence"):
        args = {"run_id": "00000000-0000-4000-8000-000000000000"}
        if tool == "verify_report":
            args["sha256"] = "a" * 64
        assert error_code(call_tool(cloud_client, key, tool, args)) == "not_found", tool
    result = call_tool(cloud_client, key, "start_run", {"upload_id": "f" * 32, "pipeline": "ecommerce_demo"})
    assert error_code(result) == "not_found"


# ================================================================ walidacja i limity narzedzi
@pytest.mark.parametrize(("tool", "args"), [
    ("list_uploads", {"limit": 1000}),
    ("list_runs", {"limit": 0}),
    ("get_report", {"run_id": "x", "format": "html"}),
    ("verify_report", {"run_id": "x", "sha256": "nope"}),
    ("start_run", {"upload_id": "../../etc", "pipeline": "ecommerce_demo"}),
    ("upload_csv", {"filename": "a" * 129 + ".csv", "content": "a;b\n1;2\n"}),
    ("upload_csv", {"filename": "dane.xlsx", "content": "a;b\n1;2\n"}),
    ("upload_csv", {"filename": "dane.csv", "content": "a;b\n", "pipeline_hint": "uksc_evidence"}),
])
def test_invalid_arguments_are_validation_errors(cloud_client, org, tool, args):
    result = call_tool(cloud_client, org.api_key, tool, args)
    assert error_code(result) == "validation_error", text_of(result)
    assert "input_value" not in text_of(result) and "Traceback" not in text_of(result)


def test_upload_content_over_the_limit_is_rejected_without_a_file(cloud_factory):
    client, settings = cloud_factory(DAAS_MCP_MAX_UPLOAD_BYTES=200)
    seeded = seed_org(settings)
    result = call_tool(client, seeded.api_key, "upload_csv", {"filename": "dane.csv", "content": CSV_TEXT})
    assert error_code(result) == "upload_rejected" and "file_too_large" in text_of(result)
    assert get_rows(settings, models.Upload, models.Upload.org_id == seeded.org_id) == []


def test_rejected_file_uses_the_v1_validation_path(cloud_client, cloud_settings, org):
    result = call_tool(cloud_client, org.api_key, "upload_csv", {"filename": "dane.csv", "content": "x;y\n1;2\n"})
    assert error_code(result) == "upload_rejected", text_of(result)
    denied = mcp_events(cloud_settings, org.org_id, "auth_denied")
    assert denied and denied[-1].target == "upload"  # ten sam audyt odrzucenia co POST /uploads


def test_plan_limit_blocks_start_run(cloud_client, cloud_settings, org):
    up = upload(cloud_client, org.api_key)

    async def exhaust(db):
        await db.merge(models.UsageCounter(org_id=org.org_id, period=current_period(), runs=20, upload_bytes=0))

    with_db(cloud_settings, exhaust)
    result = call_tool(cloud_client, org.api_key, "start_run", {"upload_id": up["id"], "pipeline": "ecommerce_demo"})
    assert error_code(result) == "plan_limit"
    denied = mcp_events(cloud_settings, org.org_id, "auth_denied")
    assert denied[-1].meta["reason"] == "plan_limit" and denied[-1].target == "mcp:start_run"


def test_upload_csv_with_auto_run_reports_the_job_and_start_run_reuses_it(cloud_client, cloud_settings, org):
    cloud_client.app.state.executor.submit = lambda job_id, org_id: None  # zadanie zostaje `queued`
    schedule = {"reminder_enabled": False, "auto_run_on_upload": True}
    r = cloud_client.put(f"{API}/orgs/{org.org_id}/schedule", json=schedule, headers=key_headers(org.api_key))
    assert r.status_code == 200, r.text

    uploaded = call_tool(cloud_client, org.api_key, "upload_csv", {"filename": "eksport.csv", "content": CSV_TEXT})
    up = ok(uploaded)
    assert up["auto_job_id"] and "do not call start_run" in text_of(uploaded)
    (job,) = get_rows(cloud_settings, models.Job)
    assert job.id == up["auto_job_id"] and job.status == "queued"

    # agent, ktory mimo to wola start_run (przeplyw z INSTRUCTIONS), dostaje to samo zadanie zamiast drugiego runu
    started = call_tool(cloud_client, org.api_key, "start_run", {"upload_id": up["id"], "pipeline": "ecommerce_demo"})
    same = ok(started)
    assert same == {"job_id": up["auto_job_id"], "status": "queued", "run_id": None, "reused": True}
    assert "already queued" in text_of(started) and len(get_rows(cloud_settings, models.Job)) == 1

    # po zakonczeniu tamtego zadania start_run tworzy nowe (ponowne przeliczenie jest dozwolone)
    update_rows(cloud_settings, models.Job, {"status": "succeeded"}, models.Job.id == job.id)
    again = ok(call_tool(cloud_client, org.api_key, "start_run", {"upload_id": up["id"], "pipeline": "ecommerce_demo"}))
    assert again["reused"] is False and again["job_id"] != job.id
    assert len(get_rows(cloud_settings, models.Job)) == 2


def test_start_run_is_rate_limited_per_key(cloud_factory):
    client, settings = cloud_factory(DAAS_RATE_LIMIT_RUN_PER_MIN=1)
    seeded = seed_org(settings)
    client.app.state.executor.submit = lambda job_id, org_id: None
    first, second = (upload(client, seeded.api_key) for _ in range(2))
    assert ok(call_tool(client, seeded.api_key, "start_run", {"upload_id": first["id"], "pipeline": "ecommerce_demo"}))
    result = call_tool(client, seeded.api_key, "start_run", {"upload_id": second["id"], "pipeline": "ecommerce_demo"})
    assert error_code(result) == "rate_limited", text_of(result)
    assert len(get_rows(settings, models.Job, models.Job.org_id == seeded.org_id)) == 1


# ================================================================ zakresy
def test_missing_scope_is_insufficient_scope_and_audited(cloud_client, cloud_settings, org):
    _, read_only = seed_key(cloud_settings, org.org_id, scopes=[sc.RUNS_READ], name="read-only")
    assert ok(call_tool(cloud_client, read_only, "whoami"))["scopes"] == [sc.RUNS_READ]
    assert ok(call_tool(cloud_client, read_only, "list_runs"))["items"] == []
    for tool, args in (("get_usage", {}), ("upload_csv", {"filename": "a.csv", "content": "a\n"}),
                       ("get_report", {"run_id": "x"})):
        result = call_tool(cloud_client, read_only, tool, args)
        assert error_code(result) == "insufficient_scope", tool
    denied = [e for e in mcp_events(cloud_settings, org.org_id, "auth_denied")
              if e.meta.get("reason") == "insufficient_scope"]
    assert {e.target for e in denied} == {"mcp:get_usage", "mcp:upload_csv", "mcp:get_report"}
    failed = [e for e in mcp_events(cloud_settings, org.org_id) if e.meta["ok"] is False]
    assert len(failed) == 3, [e.meta for e in mcp_events(cloud_settings, org.org_id)]


# ================================================================ uwierzytelnienie na poziomie HTTP
def _assert_401(r):
    assert r.status_code == 401, r.text
    # w cloud z OAuth wyzwanie niesie tez `resource_metadata` (tests/test_oauth.py); realm zawsze pierwszy
    assert r.headers["www-authenticate"].startswith('Bearer realm="daas-mcp"')
    assert r.headers["content-type"].startswith(PROBLEM)
    body = r.json()
    assert body["code"] == "unauthorized" and body["request_id"] and body["detail"]


def test_missing_key_is_401_with_www_authenticate(cloud_client, org):
    _assert_401(post(cloud_client, rpc("tools/list"), None))
    _assert_401(cloud_client.get(MCP))  # takze GET: uwierzytelnienie przed metoda


def test_invalid_and_revoked_keys_are_401(cloud_client, cloud_settings, org):
    _assert_401(post(cloud_client, rpc("tools/list"), org.api_key + "x"))
    key_id, revoked = seed_key(cloud_settings, org.org_id, revoked=True, name="old")
    _assert_401(post(cloud_client, rpc("tools/list"), revoked))
    _, expired = seed_key(cloud_settings, org.org_id, expires_in=timedelta(seconds=-1), name="expired")
    _assert_401(post(cloud_client, rpc("tools/list"), expired, scheme="x-api-key"))
    reasons = {e.meta["reason"] for e in mcp_events(cloud_settings, org.org_id, "auth_denied")}
    assert {"key_revoked", "key_expired"} <= reasons


def test_x_api_key_header_is_accepted(cloud_client, org):
    assert ok(call_tool(cloud_client, org.api_key, "whoami"))["org_id"] == org.org_id
    r = post(cloud_client, rpc("tools/call", {"name": "whoami", "arguments": {}}), org.api_key, scheme="x-api-key")
    assert r.status_code == 200 and r.json()["result"]["structuredContent"]["org_id"] == org.org_id


def test_session_cookie_is_not_accepted(cloud_client, cloud_settings, org):
    headers = {**user_headers(cloud_settings, org.user_id), **mcp_headers(None)}
    _assert_401(cloud_client.post(MCP, json=rpc("tools/list"), headers=headers))


def test_get_and_delete_are_405_after_authentication(cloud_client, org):
    for method in ("GET", "DELETE"):
        r = cloud_client.request(method, MCP, headers=mcp_headers(org.api_key))
        assert r.status_code == 405 and r.headers["allow"] == "POST", method
        assert r.json()["code"] == "method_not_allowed"


def test_body_over_the_limit_is_413(cloud_client, org):
    big = b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"pad":"' + b"a" * (3 * 1024 * 1024) + b'"}}'
    r = cloud_client.post(MCP, content=big, headers=mcp_headers(org.api_key))
    assert r.status_code == 413 and r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "payload_too_large"


def test_body_limit_is_configurable(cloud_factory):
    client, settings = cloud_factory(DAAS_MCP_MAX_BODY_BYTES=65536)
    seeded = seed_org(settings)
    padded = rpc("tools/list", {"pad": "a" * 70000})
    assert post(client, padded, seeded.api_key).status_code == 413
    assert post(client, rpc("tools/list"), seeded.api_key).status_code == 200


def test_ip_rate_limit_applies_to_mcp(cloud_factory):
    client, settings = cloud_factory(DAAS_RATE_LIMIT_IP_PER_MIN=2)
    seeded = seed_org(settings)
    codes = [post(client, rpc("tools/list"), seeded.api_key).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = post(client, rpc("tools/list"), seeded.api_key)
    assert r.json()["code"] == "rate_limited" and r.headers["retry-after"]


def test_per_key_rate_limit_applies_to_every_mcp_request(cloud_factory):
    client, settings = cloud_factory(DAAS_RATE_LIMIT_MCP_PER_MIN=2)
    seeded = seed_org(settings)
    other = seed_org(settings, name="Other", owner_email="other@example.test")
    codes = [post(client, rpc("tools/list"), seeded.api_key).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = post(client, rpc("tools/list"), seeded.api_key)
    assert r.json()["code"] == "rate_limited" and r.headers["retry-after"]
    assert post(client, rpc("tools/list"), other.api_key).status_code == 200  # kubelek per klucz, nie per adres
    assert post(client, rpc("tools/list"), None).status_code == 401  # bez klucza nadal 401, nie 429


def test_security_headers_and_no_cors_for_foreign_origins(cloud_client, org):
    r = cloud_client.post(MCP, json=rpc("tools/list"),
                          headers={**mcp_headers(org.api_key), "Origin": "https://evil.example.test"})
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-request-id"]
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "access-control-allow-origin" not in r.headers
    pre = cloud_client.options(MCP, headers={"Origin": "https://evil.example.test",
                                             "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "authorization"})
    assert "access-control-allow-origin" not in pre.headers


def test_notifications_are_accepted_with_202(cloud_client, org):
    assert post(cloud_client, notification("notifications/initialized"), org.api_key).status_code == 202


# ================================================================ konfiguracja i tryby
def test_disabled_mcp_is_a_plain_404(cloud_factory):
    client, settings = cloud_factory(DAAS_MCP_ENABLED="false")
    seeded = seed_org(settings)
    r = post(client, rpc("tools/list"), seeded.api_key)
    assert r.status_code == 404 and r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "not_found"
    assert client.app.state.mcp is None


def test_mcp_is_not_part_of_openapi(cloud_client):
    paths = cloud_client.app.openapi()["paths"]
    assert not any(p.startswith(MCP) for p in paths)


def test_selfhost_deployment_key_maps_to_the_default_org(anon_client: TestClient):
    who = ok(call_tool(anon_client, TEST_API_KEY, "whoami"))
    assert who["mode"] == "selfhost" and who["org_id"] == "default" and who["key_prefix"] is None
    assert sc.RUNS_WRITE in who["scopes"]
    _assert_401(post(anon_client, rpc("tools/list"), "wrong-key-0123456789abcdef"))


def test_selfhost_end_to_end_run(anon_client: TestClient):
    done = finished_run(anon_client, TEST_API_KEY)
    report = ok(call_tool(anon_client, TEST_API_KEY, "get_report", {"run_id": done["run_id"]}))
    assert report["content"] and report["truncated"] is False


def test_modern_protocol_tool_errors_use_the_same_format(cloud_client, org):
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {}}
    r = post(cloud_client, rpc("tools/call", {"name": "list_runs", "arguments": {"limit": 1000}, "_meta": meta}),
             org.api_key, protocol="2026-07-28", extra={"Mcp-Method": "tools/call", "Mcp-Name": "list_runs"})
    assert r.status_code == 200, r.text
    assert error_code(r.json()["result"]) == "validation_error"


def test_unexpected_failure_is_masked(cloud_client, org, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("secret detail runtime/tenants/x/daas.duckdb")

    monkeypatch.setattr(mcp_tools.v1_usage, "usage_report", boom)
    result = call_tool(cloud_client, org.api_key, "get_usage")
    assert error_code(result) == "internal_error"
    assert "secret" not in text_of(result) and "runtime/" not in text_of(result)


def test_the_same_app_can_be_started_twice(cloud_settings):
    from app.main import create_app

    app = create_app(cloud_settings)
    seeded = seed_org(cloud_settings)
    for _ in range(2):  # SDK: menedzer sesji da sie uruchomic raz; kazdy lifespan dostaje nowego
        with TestClient(app, base_url="https://testserver") as client:
            assert ok(call_tool(client, seeded.api_key, "whoami"))["org_id"] == seeded.org_id
