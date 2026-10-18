"""Izolacja najemcow dla tokenow OAuth konektorow MCP i brak sekretow OAuth w audycie, logach i bazie.

Token wydany po zgodzie w organizacji A wola KAZDE narzedzie, zasob i prompt `/mcp` z identyfikatorami organizacji B
(ten sam zestaw przypadkow co `tests/test_mcp_isolation.py` dla klucza API): obcy identyfikator = `not_found`,
listy i opisy organizacji zwracaja wylacznie dane A. Uzytkownik nalezacy do obu organizacji dostaje token zwiazany
z organizacja zgody. Kod, `code_verifier`, tokeny i sekret klienta nie trafiaja do `audit_events`, pliku audytu
najemcy, dziennika ani do kolumn tokenow i kodow (tam sa tylko skroty SHA-256).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.control import models
from app.control import scopes as sc
from app.control.security import sha256_hex
from app.control.tenancy import TenantPaths
from app.core.config import Settings
from app.main import create_app
from app.mcp_server import prompts as mcp_prompts
from app.mcp_server import resources as mcp_resources
from tests.conftest import PROJECT_ROOT, TEST_SECRET_KEY
from tests.mcp_helpers import call_tool, error_code, ok, post, rpc, text_of, wait_job
from tests.oauth_helpers import (
    ISSUER,
    Connection,
    code_from,
    connect,
    decide,
    refresh,
    register_ok,
    revoke,
    start,
    token,
)
from tests.test_mcp_isolation import CASES, PROMPT_CASES, RESOURCE_CASES, UKSC_RUN_ID
from tests.test_mcp_isolation import World as ToolWorld
from tests.test_mcp_resources import ecommerce_run, rpc_body, rpc_error, rpc_error_code
from tests.v1_helpers import (
    API,
    get_rows,
    key_headers,
    seed_member,
    seed_org,
    user_headers,
    with_db,
)

CSV_TEXT = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_text(encoding="utf-8")
#: Zakresy zgody A: odczyt i zapis, zeby kazde narzedzie doszlo do sprawdzenia organizacji (a nie zakresu).
SCOPES = "runs:read runs:write uploads:write reports:read usage:read"


@dataclass
class World:
    tools: ToolWorld  # klient, ustawienia, organizacje i zasoby B (jak w test_mcp_isolation)
    a_conn: Connection

    @property
    def client(self) -> TestClient:
        return self.tools.client

    @property
    def settings(self) -> Settings:
        return self.tools.settings


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    root: Path = tmp_path_factory.mktemp("oauth_isolation")
    settings = Settings(
        _env_file=None, daas_mode="cloud", daas_secret_key=TEST_SECRET_KEY, daas_api_key=None,
        daas_control_db_url=f"sqlite+aiosqlite:///{root / 'control.db'}",
        duckdb_path=str(root / "runtime" / "daas.duckdb"), reports_dir=str(root / "runtime" / "reports"),
        fixtures_dir=str(PROJECT_ROOT / "data" / "fixtures"), daas_cockpit_origin=ISSUER,
        daas_rate_limit_ip_per_min=0, daas_rate_limit_auth_fail_per_min=0,  # macierz wielu odmow 401
        daas_metrics_token=None, daas_allow_console_mail=True)
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
        tools = ToolWorld(client, settings, a, b, up["id"], job["job_id"], done["run_id"], run["report_sha256"]["md"])
        yield World(tools, connect(client, settings, a.user_id, a.org_id, scope=SCOPES))


def test_matrix_covers_every_tool():
    from tests.mcp_helpers import TOOL_NAMES

    assert sorted(name for name, _, _ in CASES) == sorted(TOOL_NAMES)


@pytest.mark.parametrize(("tool", "args", "expect"), CASES, ids=[c[0] for c in CASES])
def test_token_of_a_never_reaches_resources_of_b(world: World, tool, args, expect):
    result = call_tool(world.client, world.a_conn.access_token, tool, args(world.tools))
    if expect == "not_found":
        assert error_code(result) == "not_found", text_of(result)
    else:
        expect(world.tools, ok(result))
    serialized = repr(result)
    for secret in world.tools.b_secrets:
        assert secret not in serialized, (tool, secret)


# ================================================================ zasoby i prompty OAuth
@pytest.fixture
def own_requests(world: World):
    """Wlasne zasoby A w tym samym swiecie; po tescie list_runs A znow jest puste."""
    tools = world.tools
    upload_id, run_id = ecommerce_run(world.client, tools.a.api_key, filename="TEST-own.csv")
    job = ok(call_tool(world.client, tools.a.api_key, "start_run",
                       {"upload_id": upload_id, "pipeline": "ecommerce_demo"}))
    previous = wait_job(world.client, tools.a.api_key, job["job_id"])
    assert previous["status"] == "succeeded", previous
    uksc_run_id = str(uuid4())

    async def seed_uksc_run(db):
        db.add(models.RunRecord(run_id=uksc_run_id, org_id=tools.a.org_id,
                                pipeline="uksc_evidence", status="SUCCESS"))

    with_db(world.settings, seed_uksc_run)
    replacements = {tools.b_run_id: run_id, tools.b_upload_id: upload_id, UKSC_RUN_ID: uksc_run_id}
    requests = {"resources/list": ("resources/list", {})}
    for template, uri in RESOURCE_CASES:
        own_uri = uri(tools)
        for foreign, own in replacements.items():
            own_uri = own_uri.replace(foreign, own)
        requests[template] = ("resources/read", {"uri": own_uri})
    for prompt, arguments in PROMPT_CASES:
        args = {name: replacements.get(value, value) for name, value in arguments(tools).items()}
        if prompt == "compare_runs":  # kontrola pozytywna potrzebuje dwoch runow tego samego pipeline'u
            args["previous_run_id"] = previous["run_id"]
        requests[prompt] = ("prompts/get", {"name": prompt, "arguments": args})
    try:
        yield requests
    finally:
        async def remove_own_records(db):
            await db.execute(delete(models.Job).where(models.Job.org_id == tools.a.org_id,
                                                      models.Job.upload_id == upload_id))
            await db.execute(delete(models.RunRecord).where(models.RunRecord.org_id == tools.a.org_id,
                                                            models.RunRecord.run_id.in_(
                                                                (run_id, previous["run_id"], uksc_run_id))))
            await db.execute(delete(models.Upload).where(models.Upload.org_id == tools.a.org_id,
                                                         models.Upload.id == upload_id))

        with_db(world.settings, remove_own_records)


def _assert_nothing_of_b(world: World, payload, label: str) -> None:
    serialized = repr(payload)
    for secret in world.tools.b_secrets:
        assert secret not in serialized, (label, secret)


def test_oauth_matrix_covers_every_resource_and_prompt():
    assert sorted(name for name, _ in RESOURCE_CASES) == sorted(spec.shape for spec in mcp_resources.SPECS)
    assert sorted(name for name, _ in PROMPT_CASES) == sorted(spec.name for spec in mcp_prompts.SPECS)


@pytest.mark.parametrize(("template", "uri"), RESOURCE_CASES, ids=[c[0] for c in RESOURCE_CASES])
def test_token_of_a_never_reads_resources_of_b(world: World, template, uri):
    body = rpc_body(world.client, world.a_conn.access_token, "resources/read", {"uri": uri(world.tools)})
    assert "error" in body and rpc_error_code(body["error"]) == "not_found", body
    assert "data" not in body["error"]
    _assert_nothing_of_b(world, body, template)


@pytest.mark.parametrize(("prompt", "arguments"), PROMPT_CASES, ids=[c[0] for c in PROMPT_CASES])
def test_token_of_a_never_gets_prompts_about_resources_of_b(world: World, prompt, arguments):
    body = rpc_body(world.client, world.a_conn.access_token, "prompts/get",
                    {"name": prompt, "arguments": arguments(world.tools)})
    assert "error" in body and rpc_error_code(body["error"]) == "not_found", body
    assert "data" not in body["error"]
    _assert_nothing_of_b(world, body, prompt)


def test_resource_list_with_a_token_of_a_shows_nothing_of_b(world: World):
    tools = world.tools
    listed = rpc_body(world.client, world.a_conn.access_token, "resources/list")
    assert "result" in listed, listed
    _assert_nothing_of_b(world, listed, "resources/list")
    to_b = connect(world.client, world.settings, tools.b.user_id, tools.b.org_id, scope=SCOPES,
                   client_id=world.a_conn.client_id)
    own = rpc_body(world.client, to_b.access_token, "resources/list")["result"]["resources"]
    assert f"daas://runs/{tools.b_run_id}/report.md" in {r["uri"] for r in own}
    for template, uri in RESOURCE_CASES:
        body = rpc_body(world.client, to_b.access_token, "resources/read", {"uri": uri(tools)})
        assert "result" in body, (template, body)
    for prompt, arguments in PROMPT_CASES:
        if prompt != "compare_runs":  # B ma runy roznych pipeline'ow, jak w macierzy kluczy
            body = rpc_body(world.client, to_b.access_token, "prompts/get",
                            {"name": prompt, "arguments": arguments(tools)})
            assert "result" in body, (prompt, body)


def test_insufficient_scope_is_refused_for_resources_and_prompts(world: World, own_requests):
    # Osobne zgody tego samego klienta nie zuzywaja limitu dynamicznych rejestracji.
    limited = connect(world.client, world.settings, world.tools.a.user_id, world.tools.a.org_id,
                      scope="usage:read", client_id=world.a_conn.client_id)
    for case, (method, params) in own_requests.items():
        allowed = rpc_body(world.client, world.a_conn.access_token, method, params)
        assert "result" in allowed, (case, allowed)
        if method == "resources/list":
            assert allowed["result"]["resources"]  # lista ograniczona zakresem, nie brakiem danych
            body = rpc_body(world.client, limited.access_token, method, params)
            assert body["result"]["resources"] == []
        else:
            error = rpc_error(world.client, limited.access_token, method, params)
            assert rpc_error_code(error) == "insufficient_scope", (case, error)


@pytest.mark.parametrize("via", ["rfc7009", "cockpit"], ids=["rfc7009", "cockpit"])
def test_revoked_token_cannot_list_or_read_resources_or_get_prompts(world: World, own_requests, via):
    tools = world.tools
    conn = connect(world.client, world.settings, tools.a.user_id, tools.a.org_id, scope="runs:read reports:read",
                   client_id=world.a_conn.client_id)
    for case, (method, params) in own_requests.items():
        body = rpc_body(world.client, conn.access_token, method, params)
        assert "result" in body, (case, body)
    if via == "rfc7009":
        assert revoke(world.client, conn.client_id, conn.refresh_token).status_code == 200
    else:
        (grant,) = get_rows(world.settings, models.OAuthToken, models.OAuthToken.client_id == conn.client_id,
                            models.OAuthToken.token_hash == sha256_hex(conn.refresh_token))
        response = world.client.delete(f"{API}/orgs/{tools.a.org_id}/oauth/grants/{grant.id}",
                                       headers=user_headers(world.settings, tools.a.user_id))
        assert response.status_code == 204, response.text
    for case, (method, params) in own_requests.items():
        response = post(world.client, rpc(method, params), conn.access_token)
        assert response.status_code == 401, (case, response.text)
        assert response.headers["www-authenticate"].startswith("Bearer "), case


def test_degraded_role_limits_resources_immediately(world: World, own_requests):
    # Wszystkie role maja oba zakresy: degradacja nie odbierze reports:read. Sprawdzamy utrate czlonkostwa.
    assert all({sc.RUNS_READ, sc.REPORTS_READ} <= scopes for scopes in sc.ROLE_SCOPES.values())
    tools = world.tools
    user_id = seed_member(world.settings, tools.a.org_id, "removed-member@example.test", "member")
    conn = connect(world.client, world.settings, user_id, tools.a.org_id, scope="runs:read reports:read",
                   client_id=world.a_conn.client_id)
    for case, (method, params) in own_requests.items():
        body = rpc_body(world.client, conn.access_token, method, params)
        assert "result" in body, (case, body)

    async def remove_member(db):
        await db.execute(delete(models.Membership).where(models.Membership.org_id == tools.a.org_id,
                                                         models.Membership.user_id == user_id))

    with_db(world.settings, remove_member)
    for case, (method, params) in own_requests.items():
        response = post(world.client, rpc(method, params), conn.access_token)
        assert response.status_code == 401, (case, response.text)
        assert response.headers["www-authenticate"].startswith("Bearer "), case


def test_a_user_in_both_organizations_gets_a_token_bound_to_the_consented_one(world: World):
    tools = world.tools
    both = seed_member(world.settings, tools.a.org_id, "both@example.test", "admin")
    seed_member(world.settings, tools.b.org_id, "both@example.test", "member")
    to_a = connect(world.client, world.settings, both, tools.a.org_id, scope="runs:read")
    who = ok(call_tool(world.client, to_a.access_token, "whoami"))
    assert who["org_id"] == tools.a.org_id and who["auth"] == "oauth"
    assert error_code(call_tool(world.client, to_a.access_token, "get_job", {"job_id": tools.b_job_id})) == "not_found"
    assert all(r["run_id"] != tools.b_run_id
               for r in ok(call_tool(world.client, to_a.access_token, "list_runs", {"limit": 100}))["items"])
    # ta sama osoba, zgoda w B: osobny token widzi zasoby B i tylko je
    to_b = connect(world.client, world.settings, both, tools.b.org_id, scope="runs:read")
    assert ok(call_tool(world.client, to_b.access_token, "get_job", {"job_id": tools.b_job_id}))["run_id"] == tools.b_run_id
    assert ok(call_tool(world.client, to_a.access_token, "whoami"))["org_id"] == tools.a.org_id


def test_connections_of_b_are_invisible_and_untouchable_from_a(world: World):
    tools = world.tools
    to_b = connect(world.client, world.settings, tools.b.user_id, tools.b.org_id, scope="runs:read")
    (b_grant,) = [t for t in get_rows(world.settings, models.OAuthToken, models.OAuthToken.client_id == to_b.client_id,
                                      models.OAuthToken.kind == "refresh") if t.revoked_at is None]
    assert b_grant.org_id == tools.b.org_id
    listed = world.client.get(f"{API}/orgs/{tools.a.org_id}/oauth/grants", headers=key_headers(tools.a.api_key))
    assert listed.status_code == 200
    assert b_grant.id not in listed.text and to_b.client_id not in listed.text
    cross = world.client.delete(f"{API}/orgs/{tools.a.org_id}/oauth/grants/{b_grant.id}",
                                headers=key_headers(tools.a.api_key))
    assert cross.status_code == 404
    foreign = world.client.get(f"{API}/orgs/{tools.b.org_id}/oauth/grants", headers=key_headers(tools.a.api_key))
    assert foreign.status_code == 404
    assert post(world.client, rpc("tools/list"), to_b.access_token).status_code == 200  # nadal aktywne
    assert ok(call_tool(world.client, to_b.access_token, "whoami"))["org_id"] == tools.b.org_id


def test_consent_in_a_foreign_organization_is_refused(world: World):
    tools = world.tools
    client_id = register_ok(world.client)["client_id"]
    request_id, _ = start(world.client, client_id)
    r = decide(world.client, world.settings, tools.a.user_id, request_id, org_id=tools.b.org_id)
    assert r.status_code == 403
    assert get_rows(world.settings, models.OAuthCode, models.OAuthCode.id == request_id)[0].status == "pending"


def test_no_oauth_secret_in_audit_logs_or_token_columns(world: World, caplog: pytest.LogCaptureFixture):
    tools = world.tools
    secrets: list[str] = []
    with caplog.at_level(logging.INFO):  # poziom dziennika aplikacji (LOG_LEVEL); DEBUG bibliotek SQL pomijamy
        # klient poufny: sekret klienta, kod, verifier, obie pary tokenow, odswiezenie, cofniecie, wywolania /mcp
        registered = register_ok(world.client, auth_method="client_secret_post", scope="runs:read usage:read")
        client_id, client_secret = registered["client_id"], registered["client_secret"]
        request_id, verifier = start(world.client, client_id)
        code = code_from(decide(world.client, world.settings, tools.a.user_id, request_id, org_id=tools.a.org_id))
        first = token(world.client, client_id, code, verifier, client_secret=client_secret).json()
        ok(call_tool(world.client, first["access_token"], "whoami"))
        second = refresh(world.client, client_id, first["refresh_token"], client_secret=client_secret).json()
        ok(call_tool(world.client, second["access_token"], "get_usage"))
        replay = token(world.client, client_id, code, verifier, client_secret=client_secret)
        assert replay.json()["error"] == "invalid_grant"
        public = connect(world.client, world.settings, tools.a.user_id, tools.a.org_id, scope="runs:read")
        assert revoke(world.client, public.client_id, public.refresh_token).status_code == 200
        secrets += [client_secret, code, verifier, first["access_token"], first["refresh_token"],
                    second["access_token"], second["refresh_token"], public.code, public.verifier,
                    public.access_token, public.refresh_token, world.a_conn.access_token, world.a_conn.refresh_token,
                    world.a_conn.code]

    audit = get_rows(world.settings, models.AuditEvent)
    assert {"oauth.client_registered", "oauth.consent", "oauth.token_issued", "oauth.token_revoked"} <= {
        e.action for e in audit}
    dumped = repr([(e.action, e.target, e.meta, e.actor_id) for e in audit])
    lines = TenantPaths.for_org(world.settings, tools.a.org_id).audit_log
    file_text = lines.read_text(encoding="utf-8") if lines.exists() else ""
    rows = [*get_rows(world.settings, models.OAuthToken), *get_rows(world.settings, models.OAuthCode)]
    stored = repr([{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows])
    for secret in secrets:
        assert secret not in dumped, "secret in audit_events"
        assert secret not in file_text, "secret in the tenant audit file"
        assert secret not in caplog.text, "secret in the log"
        assert secret not in stored, "secret in oauth_tokens / oauth_codes"
    assert sha256_hex(first["access_token"]) in stored  # skroty sa, wartosci nie
    # sekret klienta poufnego lezy wylacznie w metadanych rejestracji (SDK porownuje go jawnie)
    (row,) = get_rows(world.settings, models.OAuthClient, models.OAuthClient.client_id == client_id)
    assert row.info["client_secret"] == client_secret
