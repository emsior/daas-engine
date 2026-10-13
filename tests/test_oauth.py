"""OAuth 2.1 dla konektorow MCP (ChatGPT, Claude): metadane RFC 8414 / RFC 9728, rejestracja dynamiczna, `/authorize`
-> zgoda w kokpicie -> `/token` z PKCE, token w `/mcp`, rotacja, cofanie, polaczone aplikacje, sprzatanie, tryby.
Izolacja najemcow i brak sekretow w audycie: `test_oauth_isolation.py`."""
from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.control import models
from app.control import scopes as sc
from app.control.oauth import ACCESS_PREFIX, REFRESH_PREFIX, REGISTER_PER_MIN, purge_expired
from app.control.security import sha256_hex
from app.control.types import utcnow
from app.core.config import Settings
from tests.conftest import PROJECT_ROOT
from tests.mcp_helpers import call_tool, error_code, mcp_headers, ok, post, rpc
from tests.oauth_helpers import (
    CLIENT_NAME,
    ISSUER,
    REDIRECT,
    RESOURCE,
    STATE,
    authorize,
    code_from,
    connect,
    consent_url,
    decide,
    pkce_pair,
    query_of,
    read_request,
    refresh,
    register,
    register_ok,
    request_id_from,
    revoke,
    start,
    token,
)
from tests.v1_helpers import (
    API,
    get_rows,
    key_headers,
    run,
    seed_key,
    seed_member,
    seed_org,
    update_rows,
    user_headers,
    with_db,
)

ALL_SCOPES = " ".join(sc.KEY_SCOPES)
PRM_URL = f"{ISSUER}/.well-known/oauth-protected-resource/mcp"
CSV_TEXT = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_text(encoding="utf-8")


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def events(settings: Settings, action: str) -> list[models.AuditEvent]:
    return get_rows(settings, models.AuditEvent, models.AuditEvent.action == action)


def tokens(settings: Settings, *where) -> list[models.OAuthToken]:
    return get_rows(settings, models.OAuthToken, *where)


def mcp_status(client: TestClient, access_token: str) -> int:
    return post(client, rpc("tools/list"), access_token).status_code


# ================================================================ metadane (RFC 8414, RFC 9728)
def test_authorization_server_metadata(cloud_client):
    r = cloud_client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["issuer"] == ISSUER  # bez ukosnika: issuer porownuje sie jako napis
    for name, path in (("authorization_endpoint", "/authorize"), ("token_endpoint", "/token"),
                       ("registration_endpoint", "/register"), ("revocation_endpoint", "/revoke")):
        assert meta[name] == ISSUER + path, name
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["response_types_supported"] == ["code"]
    assert set(meta["grant_types_supported"]) == {"authorization_code", "refresh_token"}
    assert "none" in meta["token_endpoint_auth_methods_supported"]  # klienci publiczni z PKCE
    assert meta["scopes_supported"] == list(sc.KEY_SCOPES) and sc.ORG_DELETE not in meta["scopes_supported"]


def test_protected_resource_metadata(cloud_client):
    r = cloud_client.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["resource"] == RESOURCE and meta["authorization_servers"] == [ISSUER]
    assert meta["scopes_supported"] == list(sc.KEY_SCOPES) and meta["bearer_methods_supported"] == ["header"]
    assert meta["resource_name"]


def test_mcp_challenge_points_connectors_to_the_resource_metadata(cloud_client, org):
    r = post(cloud_client, rpc("tools/list"), None)
    assert r.status_code == 401
    challenge = r.headers["www-authenticate"]
    assert challenge.startswith('Bearer realm="daas-mcp"') and f'resource_metadata="{PRM_URL}"' in challenge
    assert "error=" not in challenge  # brak tokenu to nie blad tokenu (RFC 6750 3.1)
    bad = post(cloud_client, rpc("tools/list"), org.api_key + "x")  # zly klucz API: to samo wyzwanie
    assert bad.status_code == 401 and f'resource_metadata="{PRM_URL}"' in bad.headers["www-authenticate"]


def test_ip_rate_limit_covers_the_oauth_routes(cloud_factory):
    client, _ = cloud_factory(DAAS_RATE_LIMIT_IP_PER_MIN=2)
    codes = [client.get("/.well-known/oauth-authorization-server").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = client.post("/token", data={"grant_type": "authorization_code"})
    assert r.status_code == 429 and r.headers["retry-after"]
    assert r.headers["content-type"].startswith("application/problem+json") and r.json()["code"] == "rate_limited"


# ================================================================ rejestracja dynamiczna (RFC 7591)
def test_public_client_registration(cloud_client, cloud_settings):
    body = register_ok(cloud_client)
    assert body["client_id"] and body["token_endpoint_auth_method"] == "none" and "client_secret" not in body
    assert body["client_name"] == CLIENT_NAME and body["redirect_uris"] == [REDIRECT]
    assert body["scope"] == "runs:read reports:read usage:read"  # bez `scope`: tylko odczyt
    (row,) = get_rows(cloud_settings, models.OAuthClient)
    assert row.client_id == body["client_id"] and row.client_name == CLIENT_NAME and row.redirect_uris == [REDIRECT]
    assert row.info["client_id"] == body["client_id"]
    (event,) = events(cloud_settings, "oauth.client_registered")
    assert event.org_id is None and event.actor_type == "system" and event.meta == {"client_id": body["client_id"]}


def test_confidential_client_gets_a_secret_and_must_present_it(cloud_client, cloud_settings, org):
    body = register_ok(cloud_client, auth_method="client_secret_post", scope="runs:read")
    assert body["client_secret"] and body["token_endpoint_auth_method"] == "client_secret_post"
    request_id, verifier = start(cloud_client, body["client_id"])
    code = code_from(decide(cloud_client, cloud_settings, org.user_id, request_id, org_id=org.org_id))
    missing = token(cloud_client, body["client_id"], code, verifier)
    assert missing.status_code == 401 and missing.json()["error"] == "invalid_client"
    good = token(cloud_client, body["client_id"], code, verifier, client_secret=body["client_secret"])
    assert good.status_code == 200 and good.json()["access_token"].startswith(ACCESS_PREFIX)


@pytest.mark.parametrize(("overrides", "error"), [
    ({"redirect_uris": ["http://agent.example.test/cb"]}, "invalid_redirect_uri"),  # http poza APP_ENV=dev
    ({"redirect_uris": ["http://localhost:8765/callback"]}, "invalid_redirect_uri"),
    ({"redirect_uris": ["https://agent.example.test/cb#part"]}, "invalid_redirect_uri"),
    ({"redirect_uris": ["https://user:pw@agent.example.test/cb"]}, "invalid_redirect_uri"),
    ({"redirect_uris": ["cursor://agent.example.test/cb"]}, "invalid_redirect_uri"),
    ({"redirect_uris": ["https://agent.example.test/" + "a" * 520]}, "invalid_redirect_uri"),
    ({"redirect_uris": [f"https://agent.example.test/cb{i}" for i in range(11)]}, "invalid_redirect_uri"),
    ({"grant_types": ["authorization_code", "client_credentials"]}, "invalid_client_metadata"),
    ({"grant_types": ["refresh_token"]}, "invalid_client_metadata"),
    ({"response_types": ["code", "token"]}, "invalid_client_metadata"),
    ({"scope": "runs:read org:delete"}, "invalid_client_metadata"),
    ({"auth_method": "private_key_jwt"}, "invalid_client_metadata"),
    ({"name": "x" * 201}, "invalid_client_metadata"),
])
def test_registration_is_refused(cloud_client, cloud_settings, overrides, error):
    r = register(cloud_client, **overrides)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == error
    assert get_rows(cloud_settings, models.OAuthClient) == []
    assert "Traceback" not in r.text


def test_loopback_http_redirects_are_allowed_in_dev_only(cloud_factory):
    client, settings = cloud_factory(APP_ENV="dev")
    assert register(client, redirect_uris=["http://localhost:8765/callback"]).status_code == 201
    assert register(client, redirect_uris=["http://127.0.0.1:8765/callback"]).status_code == 201
    assert register(client, redirect_uris=["http://agent.example.test/callback"]).status_code == 400
    assert len(get_rows(settings, models.OAuthClient)) == 2


def test_registration_is_limited_per_address(cloud_client):
    codes = [register(cloud_client).status_code for _ in range(REGISTER_PER_MIN + 1)]
    assert codes == [201] * REGISTER_PER_MIN + [429]
    r = register(cloud_client)
    assert r.status_code == 429 and r.headers["retry-after"] and r.json()["code"] == "rate_limited"
    assert cloud_client.get("/.well-known/oauth-authorization-server").status_code == 200  # inne trasy dzialaja


# ================================================================ /authorize
def test_authorize_redirects_to_the_consent_page(cloud_client, cloud_settings):
    client_id = register_ok(cloud_client, scope=ALL_SCOPES)["client_id"]
    _, challenge = pkce_pair()
    r = authorize(cloud_client, client_id, challenge, scope="runs:read uploads:write")
    request_id = request_id_from(r)
    assert r.headers["cache-control"] == "no-store"
    (row,) = get_rows(cloud_settings, models.OAuthCode)
    assert row.id == request_id and row.status == "pending" and row.client_id == client_id
    assert row.scopes == ["runs:read", "uploads:write"] and row.code_challenge == challenge
    assert row.redirect_uri == REDIRECT and row.state == STATE and row.resource == RESOURCE
    assert row.org_id is None and row.user_id is None and row.code_hash is None
    assert timedelta(seconds=590) < row.expires_at - row.created_at <= timedelta(seconds=600)


def test_authorize_without_scope_asks_for_the_registered_scopes(cloud_client, cloud_settings):
    client_id = register_ok(cloud_client)["client_id"]
    request_id_from(authorize(cloud_client, client_id, pkce_pair()[1]))
    (row,) = get_rows(cloud_settings, models.OAuthCode)
    assert row.scopes == ["runs:read", "reports:read", "usage:read"]


def test_unregistered_redirect_uri_is_an_error_without_redirect(cloud_client, cloud_settings):
    client_id = register_ok(cloud_client)["client_id"]
    r = authorize(cloud_client, client_id, pkce_pair()[1], redirect_uri="https://evil.example.test/cb")
    assert r.status_code == 400 and "location" not in r.headers
    assert r.json()["error"] == "invalid_request"
    unknown = authorize(cloud_client, "no-such-client", pkce_pair()[1])
    assert unknown.status_code == 400 and "location" not in unknown.headers
    assert get_rows(cloud_settings, models.OAuthCode) == []


@pytest.mark.parametrize(("challenge", "method"), [(None, "S256"), ("x" * 43, "plain"), ("too-short", "S256"),
                                                   ("+" * 43, "S256")])
def test_authorize_without_a_valid_pkce_challenge_is_refused(cloud_client, cloud_settings, challenge, method):
    client_id = register_ok(cloud_client)["client_id"]
    r = authorize(cloud_client, client_id, challenge, method=method)
    assert r.status_code == 302  # klient i adres powrotu sa poprawne: blad wraca do aplikacji (RFC 6749 4.1.2.1)
    query = query_of(r.headers["location"])
    assert query["error"] == "invalid_request" and query["state"] == STATE
    assert get_rows(cloud_settings, models.OAuthCode) == []


def test_scope_outside_the_registration_is_invalid_scope(cloud_client, cloud_settings):
    client_id = register_ok(cloud_client)["client_id"]  # zarejestrowany tylko do odczytu
    r = authorize(cloud_client, client_id, pkce_pair()[1], scope="runs:read uploads:write")
    assert r.status_code == 302 and query_of(r.headers["location"])["error"] == "invalid_scope"
    assert get_rows(cloud_settings, models.OAuthCode) == []


# ================================================================ ekran zgody
def test_consent_request_lists_organizations_and_scopes_each_role_can_grant(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Second", owner_email="second-owner@example.test")
    seed_member(cloud_settings, other.org_id, org.email, "member")
    client_id = register_ok(cloud_client, scope=ALL_SCOPES)["client_id"]
    request_id, _ = start(cloud_client, client_id, scope="runs:read uploads:write org:manage")
    r = read_request(cloud_client, cloud_settings, org.user_id, request_id)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["request_id"] == request_id and body["client_id"] == client_id and body["client_name"] == CLIENT_NAME
    assert body["scopes_requested"] == ["runs:read", "uploads:write", "org:manage"]
    assert body["redirect_host"] == "agent.example.test" and body["expires_at"]
    by_org = {o["org_id"]: o for o in body["organizations"]}
    assert by_org[org.org_id]["role"] == "owner" and by_org[org.org_id]["name"] == "Acme"
    assert by_org[org.org_id]["scopes_grantable"] == ["runs:read", "uploads:write", "org:manage"]
    assert by_org[other.org_id]["role"] == "member"
    assert by_org[other.org_id]["scopes_grantable"] == ["runs:read", "uploads:write"]  # member nie da org:manage


def test_consent_needs_a_signed_in_user(cloud_client, cloud_settings, org):
    client_id = register_ok(cloud_client)["client_id"]
    request_id, _ = start(cloud_client, client_id)
    assert cloud_client.get(consent_url(request_id)).status_code == 401
    as_key = cloud_client.get(consent_url(request_id), headers=key_headers(org.api_key))
    assert as_key.status_code == 403 and as_key.json()["code"] == "user_session_required"
    decision = cloud_client.post(f"{consent_url(request_id)}/decision", json={"approve": True, "org_id": org.org_id},
                                 headers=key_headers(org.api_key))
    assert decision.status_code == 403
    no_csrf = {"Cookie": user_headers(cloud_settings, org.user_id)["Cookie"]}
    r = decide(cloud_client, cloud_settings, org.user_id, request_id, org_id=org.org_id, headers=no_csrf)
    assert r.status_code == 403 and r.json()["code"] == "csrf_failed"
    assert get_rows(cloud_settings, models.OAuthCode)[0].status == "pending"


def test_unknown_decided_and_expired_requests(cloud_client, cloud_settings, org):
    assert read_request(cloud_client, cloud_settings, org.user_id, "01ARZ3NDEKTSV4RRFFQ69G5FAV").status_code == 404
    client_id = register_ok(cloud_client)["client_id"]
    expired, _ = start(cloud_client, client_id)
    update_rows(cloud_settings, models.OAuthCode, {"expires_at": utcnow() - timedelta(seconds=1)},
                models.OAuthCode.id == expired)
    r = read_request(cloud_client, cloud_settings, org.user_id, expired)
    assert r.status_code == 410 and r.json()["code"] == "expired"
    late = decide(cloud_client, cloud_settings, org.user_id, expired, org_id=org.org_id)
    assert late.status_code == 410
    decided, _ = start(cloud_client, client_id)
    assert decide(cloud_client, cloud_settings, org.user_id, decided, approve=False).status_code == 200
    assert read_request(cloud_client, cloud_settings, org.user_id, decided).status_code == 404


def test_deny_returns_access_denied_with_state_and_only_once(cloud_client, cloud_settings, org):
    client_id = register_ok(cloud_client)["client_id"]
    request_id, _ = start(cloud_client, client_id)
    r = decide(cloud_client, cloud_settings, org.user_id, request_id, approve=False)
    assert r.status_code == 200, r.text
    redirect = r.json()["redirect_to"]
    assert redirect.startswith("https://agent.example.test/oauth/callback?")
    assert query_of(redirect) == {"tenant": "1", "error": "access_denied", "state": STATE}
    (row,) = get_rows(cloud_settings, models.OAuthCode)
    assert row.status == "denied" and row.code_hash is None
    again = decide(cloud_client, cloud_settings, org.user_id, request_id, org_id=org.org_id)
    assert again.status_code == 409 and again.json()["code"] == "request_already_decided"
    assert events(cloud_settings, "oauth.consent") == []


def test_approval_rules(cloud_client, cloud_settings, org):
    outsider = seed_org(cloud_settings, name="Outsider", owner_email="outsider@example.test")
    member_id = seed_member(cloud_settings, org.org_id, "member@example.test", "member")
    client_id = register_ok(cloud_client, scope=ALL_SCOPES)["client_id"]
    request_id, _ = start(cloud_client, client_id, scope="org:manage")
    missing = decide(cloud_client, cloud_settings, org.user_id, request_id)
    assert missing.status_code == 422 and missing.json()["errors"][0]["loc"] == ["body", "org_id"]
    foreign = decide(cloud_client, cloud_settings, outsider.user_id, request_id, org_id=org.org_id)
    assert foreign.status_code == 403  # nie-czlonek organizacji
    nothing = decide(cloud_client, cloud_settings, member_id, request_id, org_id=org.org_id)
    assert nothing.status_code == 422 and nothing.json()["code"] == "no_grantable_scopes"
    assert get_rows(cloud_settings, models.OAuthCode)[0].status == "pending"  # nadal do decyzji


def test_approved_scopes_are_narrowed_to_the_request_and_the_role(cloud_client, cloud_settings, org):
    member_id = seed_member(cloud_settings, org.org_id, "member@example.test", "member")
    client_id = register_ok(cloud_client, scope=ALL_SCOPES)["client_id"]
    request_id, _ = start(cloud_client, client_id, scope="runs:read reports:read org:manage")
    r = decide(cloud_client, cloud_settings, member_id, request_id, org_id=org.org_id,
               scopes=["reports:read", "org:manage", "audit:read"])
    code_from(r)
    (row,) = get_rows(cloud_settings, models.OAuthCode)
    assert row.status == "approved" and row.scopes == ["reports:read"] and row.org_id == org.org_id
    assert row.user_id == member_id and row.code_hash and timedelta(0) < row.expires_at - utcnow() <= timedelta(
        seconds=60)
    (event,) = events(cloud_settings, "oauth.consent")
    assert event.org_id == org.org_id and event.actor_type == "user" and event.actor_id == member_id
    assert event.meta == {"client_id": client_id, "scopes": ["reports:read"]}


# ================================================================ /token: kod z PKCE
def test_code_exchange_with_pkce(cloud_client, cloud_settings, org):
    client_id = register_ok(cloud_client, scope=ALL_SCOPES)["client_id"]
    request_id, verifier = start(cloud_client, client_id, scope="runs:read usage:read")
    code = code_from(decide(cloud_client, cloud_settings, org.user_id, request_id, org_id=org.org_id))
    wrong = token(cloud_client, client_id, code, pkce_pair()[0])
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
    r = token(cloud_client, client_id, code, verifier)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600 and body["scope"] == "runs:read usage:read"
    assert body["access_token"].startswith(ACCESS_PREFIX) and body["refresh_token"].startswith(REFRESH_PREFIX)
    rows = {t.kind: t for t in tokens(cloud_settings)}
    assert rows["access"].token_hash == sha256_hex(body["access_token"])  # w bazie tylko skroty
    assert rows["refresh"].token_hash == sha256_hex(body["refresh_token"])
    assert rows["access"].parent_id == rows["refresh"].id and rows["refresh"].parent_id == request_id
    for row in rows.values():
        assert row.org_id == org.org_id and row.user_id == org.user_id and row.client_id == client_id
        assert row.scopes == ["runs:read", "usage:read"] and row.resource == RESOURCE
    assert get_rows(cloud_settings, models.OAuthCode)[0].status == "exchanged"
    (issued,) = events(cloud_settings, "oauth.token_issued")
    assert issued.org_id == org.org_id and issued.actor_id == org.user_id
    assert issued.meta == {"client_id": client_id, "grant": "authorization_code", "scopes": ["runs:read", "usage:read"]}


def test_reused_code_is_invalid_grant_and_revokes_the_tokens_it_issued(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    assert mcp_status(cloud_client, conn.access_token) == 200
    replay = token(cloud_client, conn.client_id, conn.code, conn.verifier)
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert mcp_status(cloud_client, conn.access_token) == 401
    assert refresh(cloud_client, conn.client_id, conn.refresh_token).json()["error"] == "invalid_grant"
    assert all(t.revoked_at is not None for t in tokens(cloud_settings))
    (event,) = events(cloud_settings, "oauth.token_revoked")
    assert event.meta["reason"] == "code_replay" and event.meta["count"] == 2 and event.actor_type == "system"


def test_reused_code_also_revokes_tokens_rotated_since(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    rotated = refresh(cloud_client, conn.client_id, conn.refresh_token).json()
    assert token(cloud_client, conn.client_id, conn.code, conn.verifier).json()["error"] == "invalid_grant"
    assert mcp_status(cloud_client, rotated["access_token"]) == 401


def test_code_expires_sixty_seconds_after_consent(cloud_client, cloud_settings, org):
    client_id = register_ok(cloud_client)["client_id"]
    request_id, verifier = start(cloud_client, client_id)
    code = code_from(decide(cloud_client, cloud_settings, org.user_id, request_id, org_id=org.org_id))
    update_rows(cloud_settings, models.OAuthCode, {"expires_at": utcnow() - timedelta(seconds=1)},
                models.OAuthCode.id == request_id)
    r = token(cloud_client, client_id, code, verifier)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert tokens(cloud_settings) == []


def test_code_of_another_client_is_invalid_grant(cloud_client, cloud_settings, org):
    first = register_ok(cloud_client)["client_id"]
    second = register_ok(cloud_client)["client_id"]
    request_id, verifier = start(cloud_client, first)
    code = code_from(decide(cloud_client, cloud_settings, org.user_id, request_id, org_id=org.org_id))
    r = token(cloud_client, second, code, verifier)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert token(cloud_client, first, code, verifier).status_code == 200  # kod nadal wazny dla wlasciwego klienta


# ================================================================ token w /mcp
def test_access_token_works_in_mcp_for_the_consented_org_and_scopes(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    who = ok(call_tool(cloud_client, conn.access_token, "whoami"))
    assert who["auth"] == "oauth" and who["client_id"] == conn.client_id and who["key_prefix"] is None
    assert who["org_id"] == org.org_id and who["scopes"] == ["runs:read"]
    assert ok(call_tool(cloud_client, conn.access_token, "list_runs"))["items"] == []
    assert error_code(call_tool(cloud_client, conn.access_token, "get_usage")) == "insufficient_scope"
    denied = [e for e in events(cloud_settings, "auth_denied") if e.meta.get("reason") == "insufficient_scope"]
    assert denied and denied[-1].actor_type == "user" and denied[-1].actor_id == org.user_id
    calls = events(cloud_settings, "mcp.tool")
    assert calls and all(e.actor_type == "user" and e.actor_id == org.user_id for e in calls)
    (access,) = tokens(cloud_settings, models.OAuthToken.kind == "access")
    assert access.last_used_at is not None
    # klucz API dalej dziala obok tokenu: whoami odroznia sposob uwierzytelnienia
    assert ok(call_tool(cloud_client, org.api_key, "whoami"))["auth"] == "api_key"
    assert ok(call_tool(cloud_client, conn.access_token, "whoami"))["auth"] == "oauth"


def test_x_api_key_header_carries_an_oauth_token_too(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    r = post(cloud_client, rpc("tools/call", {"name": "whoami", "arguments": {}}), conn.access_token,
             scheme="x-api-key")
    assert r.status_code == 200 and r.json()["result"]["structuredContent"]["auth"] == "oauth"


def test_upload_and_run_through_oauth_act_as_the_user(cloud_client, cloud_settings, org):
    cloud_client.app.state.executor.submit = lambda job_id, org_id: None  # zadanie zostaje `queued`
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="uploads:write runs:write runs:read")
    up = ok(call_tool(cloud_client, conn.access_token, "upload_csv", {"filename": "eksport.csv", "content": CSV_TEXT}))
    (created,) = events(cloud_settings, "upload.created")
    assert created.actor_type == "user" and created.actor_id == org.user_id and created.org_id == org.org_id
    job = ok(call_tool(cloud_client, conn.access_token, "start_run",
                       {"upload_id": up["id"], "pipeline": "ecommerce_demo"}))
    (row,) = get_rows(cloud_settings, models.Job, models.Job.id == job["job_id"])
    assert row.created_by == f"user:{org.user_id}" and row.org_id == org.org_id


def test_token_never_exceeds_the_current_role(cloud_client, cloud_settings, org):
    admin_id = seed_member(cloud_settings, org.org_id, "admin@example.test", "admin")
    conn = connect(cloud_client, cloud_settings, admin_id, org.org_id, scope="runs:read audit:read")
    assert ok(call_tool(cloud_client, conn.access_token, "whoami"))["scopes"] == ["audit:read", "runs:read"]
    update_rows(cloud_settings, models.Membership, {"role": "member"}, models.Membership.user_id == admin_id)
    assert ok(call_tool(cloud_client, conn.access_token, "whoami"))["scopes"] == ["runs:read"]

    async def remove(db):
        await db.execute(delete(models.Membership).where(models.Membership.user_id == admin_id))

    with_db(cloud_settings, remove)
    assert mcp_status(cloud_client, conn.access_token) == 401
    # odswiezenie po utracie czlonkostwa: odmowa i cofniecie zgody
    assert refresh(cloud_client, conn.client_id, conn.refresh_token).json()["error"] == "invalid_grant"
    assert all(t.revoked_at is not None for t in tokens(cloud_settings))
    assert events(cloud_settings, "oauth.token_revoked")[-1].meta["reason"] == "membership_ended"


def test_expired_access_token_is_401_with_invalid_token_challenge(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    update_rows(cloud_settings, models.OAuthToken, {"expires_at": utcnow() - timedelta(seconds=1)},
                models.OAuthToken.kind == "access")
    r = post(cloud_client, rpc("tools/list"), conn.access_token)
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    challenge = r.headers["www-authenticate"]
    assert 'error="invalid_token"' in challenge and f'resource_metadata="{PRM_URL}"' in challenge
    garbage = post(cloud_client, rpc("tools/list"), ACCESS_PREFIX + "x" * 43)
    assert garbage.status_code == 401 and 'error="invalid_token"' in garbage.headers["www-authenticate"]


# ================================================================ odswiezanie (rotacja)
def test_refresh_rotates_the_pair(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read usage:read")
    r = refresh(cloud_client, conn.client_id, conn.refresh_token, scope="runs:read")
    assert r.status_code == 200, r.text
    rotated = r.json()
    assert rotated["scope"] == "runs:read" and rotated["refresh_token"] != conn.refresh_token
    assert refresh(cloud_client, conn.client_id, conn.refresh_token).json()["error"] == "invalid_grant"
    assert mcp_status(cloud_client, conn.access_token) == 401  # stary token dostepu cofniety razem z rotacja
    assert ok(call_tool(cloud_client, rotated["access_token"], "whoami"))["scopes"] == ["runs:read"]
    # nowy token odswiezania niesie cala zgode (RFC 6749 6): bez `scope` wraca pelny zestaw
    full = refresh(cloud_client, conn.client_id, rotated["refresh_token"]).json()
    assert full["scope"] == "runs:read usage:read"
    wider = refresh(cloud_client, conn.client_id, full["refresh_token"], scope="runs:read uploads:write")
    assert wider.status_code == 400 and wider.json()["error"] == "invalid_scope"
    assert refresh(cloud_client, conn.client_id, full["refresh_token"]).status_code == 200  # nie zuzyty
    grants = [e.meta["grant"] for e in events(cloud_settings, "oauth.token_issued")]
    assert grants == ["authorization_code", "refresh_token", "refresh_token", "refresh_token"]


def test_refresh_token_of_another_client_is_invalid_grant(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    other = register_ok(cloud_client)["client_id"]
    assert refresh(cloud_client, other, conn.refresh_token).json()["error"] == "invalid_grant"
    assert refresh(cloud_client, conn.client_id, conn.access_token).json()["error"] == "invalid_grant"
    assert refresh(cloud_client, conn.client_id, conn.refresh_token).status_code == 200


# ================================================================ cofanie (RFC 7009)
def test_revoking_the_refresh_token_revokes_its_access_tokens(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    r = revoke(cloud_client, conn.client_id, conn.refresh_token)  # klient publiczny: bez pola client_secret
    assert r.status_code == 200, r.text
    assert mcp_status(cloud_client, conn.access_token) == 401
    assert refresh(cloud_client, conn.client_id, conn.refresh_token).json()["error"] == "invalid_grant"
    (event,) = events(cloud_settings, "oauth.token_revoked")
    assert event.meta == {"client_id": conn.client_id, "reason": "client_revoked", "count": 2}
    assert event.org_id == org.org_id and event.actor_id == org.user_id
    assert revoke(cloud_client, conn.client_id, conn.refresh_token).status_code == 200  # ponownie: 200 bez zmian
    assert len(events(cloud_settings, "oauth.token_revoked")) == 1


def test_revoking_an_access_token_keeps_the_grant(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    assert revoke(cloud_client, conn.client_id, conn.access_token, hint="access_token").status_code == 200
    assert mcp_status(cloud_client, conn.access_token) == 401
    renewed = refresh(cloud_client, conn.client_id, conn.refresh_token).json()
    assert mcp_status(cloud_client, renewed["access_token"]) == 200


def test_another_client_cannot_revoke_a_token(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    other = register_ok(cloud_client)["client_id"]
    assert revoke(cloud_client, other, conn.refresh_token).status_code == 200  # RFC 7009: zawsze 200
    assert mcp_status(cloud_client, conn.access_token) == 200
    assert events(cloud_settings, "oauth.token_revoked") == []


# ================================================================ polaczone aplikacje (kokpit)
def test_connected_apps_list_and_disconnect(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    assert mcp_status(cloud_client, conn.access_token) == 200
    url = f"{API}/orgs/{org.org_id}/oauth/grants"
    owner = user_headers(cloud_settings, org.user_id)
    r = cloud_client.get(url, headers=owner)
    assert r.status_code == 200, r.text
    (grant,) = r.json()
    (first_refresh,) = tokens(cloud_settings, models.OAuthToken.kind == "refresh")
    assert grant["token_id"] == first_refresh.id and grant["client_id"] == conn.client_id
    assert grant["client_name"] == CLIENT_NAME and grant["user_id"] == org.user_id and grant["role"] == "owner"
    assert grant["scopes"] == ["runs:read"] and grant["last_used_at"] and grant["expires_at"]
    assert conn.refresh_token not in r.text and conn.access_token not in r.text

    rotated = refresh(cloud_client, conn.client_id, conn.refresh_token).json()
    (after,) = cloud_client.get(url, headers=owner).json()
    assert after["token_id"] != first_refresh.id and after["last_used_at"]

    # odlaczenie po starym id (lista sprzed rotacji) i tak odcina biezaca pare
    d = cloud_client.delete(f"{url}/{first_refresh.id}", headers=owner)
    assert d.status_code == 204, d.text
    assert mcp_status(cloud_client, rotated["access_token"]) == 401
    assert refresh(cloud_client, conn.client_id, rotated["refresh_token"]).json()["error"] == "invalid_grant"
    assert cloud_client.get(url, headers=owner).json() == []
    (event,) = [e for e in events(cloud_settings, "oauth.token_revoked") if e.meta["reason"] == "disconnected"]
    assert event.actor_type == "user" and event.actor_id == org.user_id and event.meta["client_id"] == conn.client_id
    assert event.meta["count"] == 2
    assert cloud_client.delete(f"{url}/{first_refresh.id}", headers=owner).status_code == 204  # idempotentne
    assert cloud_client.delete(f"{url}/01ARZ3NDEKTSV4RRFFQ69G5FAV", headers=owner).status_code == 404


def test_connected_apps_need_org_manage(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    (grant,) = tokens(cloud_settings, models.OAuthToken.kind == "refresh")
    url = f"{API}/orgs/{org.org_id}/oauth/grants"
    member = user_headers(cloud_settings, seed_member(cloud_settings, org.org_id, "member@example.test", "member"))
    assert cloud_client.get(url, headers=member).status_code == 403
    assert cloud_client.delete(f"{url}/{grant.id}", headers=member).status_code == 403
    _, reader = seed_key(cloud_settings, org.org_id, scopes=[sc.RUNS_READ], name="reader")
    assert cloud_client.get(url, headers=key_headers(reader)).status_code == 403
    assert cloud_client.get(url, headers=key_headers(org.api_key)).status_code == 200  # klucz z org:manage
    assert mcp_status(cloud_client, conn.access_token) == 200  # nic nie zostalo cofniete


def test_access_token_id_is_not_a_connection(cloud_client, cloud_settings, org):
    connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    (access,) = tokens(cloud_settings, models.OAuthToken.kind == "access")
    r = cloud_client.delete(f"{API}/orgs/{org.org_id}/oauth/grants/{access.id}", headers=key_headers(org.api_key))
    assert r.status_code == 404


# ================================================================ sprzatanie
def test_purge_removes_expired_codes_and_tokens(cloud_client, cloud_settings, org):
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    start(cloud_client, conn.client_id)  # swieze zadanie: zostaje
    now = utcnow()
    update_rows(cloud_settings, models.OAuthCode, {"expires_at": now - timedelta(days=2)},
                models.OAuthCode.id == conn.request_id)
    update_rows(cloud_settings, models.OAuthToken, {"expires_at": now - timedelta(days=31)},
                models.OAuthToken.kind == "access")

    async def purge(db):
        return await purge_expired(db, now)

    assert with_db(cloud_settings, purge) == (1, 1, 0)
    assert [c.status for c in get_rows(cloud_settings, models.OAuthCode)] == ["pending"]
    assert [t.kind for t in tokens(cloud_settings)] == ["refresh"]
    assert with_db(cloud_settings, purge) == (0, 0, 0)


def test_purge_removes_clients_left_without_codes_and_tokens(cloud_client, cloud_settings, org):
    """Rejestracja `/register` jest anonimowa: klient bez zadnego zadania/kodu i tokenu znika dobe po rejestracji,
    klient z aktywnym polaczeniem albo swiezym zadaniem zostaje (QS2-01)."""
    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    abandoned_fresh = register_ok(cloud_client)["client_id"]
    abandoned_old = register_ok(cloud_client)["client_id"]
    pending_old = register_ok(cloud_client)["client_id"]
    start(cloud_client, pending_old)
    now = utcnow()
    update_rows(cloud_settings, models.OAuthClient, {"created_at": now - timedelta(days=2)},
                models.OAuthClient.client_id.in_([abandoned_old, pending_old, conn.client_id]))

    async def purge(db):
        return await purge_expired(db, now)

    assert with_db(cloud_settings, purge) == (0, 0, 1)
    left = {c.client_id for c in get_rows(cloud_settings, models.OAuthClient)}
    assert left == {conn.client_id, abandoned_fresh, pending_old}
    assert mcp_status(cloud_client, conn.access_token) == 200

    # zadanie `pending_old` wygasa i po dobie jest sprzatane -> w nastepnym przebiegu znika tez jego klient
    update_rows(cloud_settings, models.OAuthCode, {"expires_at": now - timedelta(days=2)},
                models.OAuthCode.client_id == pending_old)
    assert with_db(cloud_settings, purge) == (1, 0, 1)
    assert {c.client_id for c in get_rows(cloud_settings, models.OAuthClient)} == {conn.client_id, abandoned_fresh}
    assert register(cloud_client).status_code == 201  # sprzatniety klient rejestruje sie od nowa


def test_retention_loop_purges_oauth_rows(cloud_client, cloud_settings, org):
    from app.control.db import create_engine, make_sessionmaker
    from app.control.retention import purge_oauth

    conn = connect(cloud_client, cloud_settings, org.user_id, org.org_id, scope="runs:read")
    update_rows(cloud_settings, models.OAuthToken, {"expires_at": utcnow() - timedelta(days=40)})

    async def go():
        engine = create_engine(cloud_settings)
        try:
            return await purge_oauth(make_sessionmaker(engine))
        finally:
            await engine.dispose()

    assert run(go()) == (0, 2, 0)
    assert tokens(cloud_settings) == []
    assert mcp_status(cloud_client, conn.access_token) == 401


# ================================================================ caly przeplyw w jednym scenariuszu
def test_oauth_full_flow_end_to_end(cloud_client, cloud_settings, org):
    """Jeden scenariusz od rejestracji po odciecie (T2-01): DCR -> /authorize -> zgoda -> /token -> /mcp ->
    odswiezenie (stara para martwa, nowa zyje) -> /revoke -> /mcp 401 -> zadne odswiezenie juz nie dziala; w bazie
    nie ma jawnych sekretow, a audyt ma komplet zdarzen w kolejnosci."""
    client_id = register_ok(cloud_client, scope="runs:read reports:read")["client_id"]
    assert register(cloud_client).status_code == 201  # inny klient nie przeszkadza
    request_id, verifier = start(cloud_client, client_id, scope="runs:read")
    pending = read_request(cloud_client, cloud_settings, org.user_id, request_id).json()
    assert pending["client_name"] == CLIENT_NAME and pending["redirect_host"] == "agent.example.test"
    assert [o["org_id"] for o in pending["organizations"]] == [org.org_id]
    code = code_from(decide(cloud_client, cloud_settings, org.user_id, request_id, org_id=org.org_id))

    first = token(cloud_client, client_id, code, verifier)
    assert first.status_code == 200, first.text
    pair = first.json()
    assert pair["token_type"].lower() == "bearer" and pair["scope"] == "runs:read"
    assert pair["access_token"].startswith(ACCESS_PREFIX) and pair["refresh_token"].startswith(REFRESH_PREFIX)
    assert mcp_status(cloud_client, pair["access_token"]) == 200
    who = ok(call_tool(cloud_client, pair["access_token"], "whoami"))
    assert (who["auth"], who["org_id"], who["client_id"]) == ("oauth", org.org_id, client_id)

    rotated = refresh(cloud_client, client_id, pair["refresh_token"])
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()
    assert second["access_token"] != pair["access_token"] and second["refresh_token"] != pair["refresh_token"]
    assert mcp_status(cloud_client, pair["access_token"]) == 401  # stara para cofnieta przez rotacje
    assert mcp_status(cloud_client, second["access_token"]) == 200
    assert refresh(cloud_client, client_id, pair["refresh_token"]).json()["error"] == "invalid_grant"

    assert revoke(cloud_client, client_id, second["refresh_token"], hint="refresh_token").status_code == 200
    assert mcp_status(cloud_client, second["access_token"]) == 401
    assert refresh(cloud_client, client_id, second["refresh_token"]).json()["error"] == "invalid_grant"
    assert cloud_client.get(f"{API}/orgs/{org.org_id}/oauth/grants",
                            headers=user_headers(cloud_settings, org.user_id)).json() == []

    rows = tokens(cloud_settings)
    assert len(rows) == 4 and all(t.revoked_at is not None for t in rows)
    secrets = {pair["access_token"], pair["refresh_token"], second["access_token"], second["refresh_token"], code}
    assert {t.token_hash for t in rows} == {sha256_hex(v) for v in secrets if v != code}
    stored = get_rows(cloud_settings, models.OAuthCode, models.OAuthCode.id == request_id)[0]
    assert stored.status == "exchanged" and stored.code_hash == sha256_hex(code)
    for event in get_rows(cloud_settings, models.AuditEvent):
        assert not (secrets & set(map(str, event.meta.values()))), event.action
    actions = [e.action for e in get_rows(cloud_settings, models.AuditEvent) if e.action.startswith("oauth.")]
    assert actions == ["oauth.client_registered", "oauth.client_registered", "oauth.consent", "oauth.token_issued",
                       "oauth.token_issued", "oauth.token_revoked"]


# ================================================================ tryby i konfiguracja
def test_selfhost_has_no_oauth(anon_client: TestClient):
    assert anon_client.app.state.oauth is None
    for path in ("/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource/mcp",
                 "/authorize"):
        assert anon_client.get(path).status_code == 404, path
    assert anon_client.post("/register", json={"redirect_uris": [REDIRECT]}).status_code == 404
    r = anon_client.post("/mcp", json=rpc("tools/list"), headers=mcp_headers(None))
    assert r.status_code == 401 and r.headers["www-authenticate"] == 'Bearer realm="daas-mcp"'


def test_disabled_flag_removes_the_routes(cloud_factory):
    client, settings = cloud_factory(DAAS_OAUTH_ENABLED="false")
    assert client.app.state.oauth is None
    assert client.get("/.well-known/oauth-authorization-server").status_code == 404
    assert register(client).status_code == 404
    r = post(client, rpc("tools/list"), ACCESS_PREFIX + "x" * 43)
    assert r.status_code == 401 and r.headers["www-authenticate"] == 'Bearer realm="daas-mcp"'


@pytest.mark.parametrize("env", [{"DAAS_COCKPIT_ORIGIN": ""},
                                 {"DAAS_OAUTH_ISSUER_URL": "http://cockpit.example.test"},
                                 {"DAAS_OAUTH_ISSUER_URL": "https://cockpit.example.test/sub"}])
def test_missing_or_invalid_issuer_turns_oauth_off_with_a_warning(cloud_factory, caplog, env):
    with caplog.at_level(logging.WARNING, logger="app.control.oauth"):
        client, _ = cloud_factory(**env)
    assert client.app.state.oauth is None
    assert client.get("/.well-known/oauth-authorization-server").status_code == 404
    assert any("OAuth for MCP connectors is off" in rec.getMessage() for rec in caplog.records)


def test_issuer_setting_overrides_the_cockpit_origin(cloud_factory):
    client, settings = cloud_factory(DAAS_OAUTH_ISSUER_URL="https://raporty.example.test/")
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["issuer"] == "https://raporty.example.test"
    assert meta["token_endpoint"] == "https://raporty.example.test/token"
    prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == "https://raporty.example.test/mcp"
    client_id = register_ok(client)["client_id"]
    r = authorize(client, client_id, pkce_pair()[1], resource="https://raporty.example.test/mcp")
    assert r.headers["location"].startswith(f"{ISSUER}/oauth/consent?request=")  # ekran zgody: kokpit
