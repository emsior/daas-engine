"""Izolacja najemcow dla tokenow OAuth konektorow MCP i brak sekretow OAuth w audycie, logach i bazie.

Token wydany po zgodzie w organizacji A wola KAZDE narzedzie `/mcp` z identyfikatorami zasobow organizacji B
(ten sam zestaw przypadkow co `tests/test_mcp_isolation.py` dla klucza API): obcy identyfikator = `not_found`,
listy i opisy organizacji zwracaja wylacznie dane A. Uzytkownik nalezacy do obu organizacji dostaje token zwiazany
z organizacja zgody. Kod, `code_verifier`, tokeny i sekret klienta nie trafiaja do `audit_events`, pliku audytu
najemcy, dziennika ani do kolumn tokenow i kodow (tam sa tylko skroty SHA-256).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control.security import sha256_hex
from app.control.tenancy import TenantPaths
from app.core.config import Settings
from app.main import create_app
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
from tests.test_mcp_isolation import CASES, UKSC_RUN_ID
from tests.test_mcp_isolation import World as ToolWorld
from tests.v1_helpers import API, get_rows, key_headers, seed_member, seed_org, with_db

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
