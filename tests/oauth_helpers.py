"""Pomocnicy testow OAuth 2.1 konektorow MCP: rejestracja dynamiczna (RFC 7591), PKCE S256, `/authorize` -> zgoda
w kokpicie (`/api/v1/oauth/requests/...`) -> `/token`, odswiezanie i cofanie. Dane wylacznie syntetyczne."""
from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.v1_helpers import API, user_headers

ISSUER = "https://cockpit.example.test"  # DAAS_COCKPIT_ORIGIN fixture'a `cloud_settings` = issuer OAuth
#: Adres powrotu konektora z wlasnym query (decyzja musi je zachowac).
REDIRECT = "https://agent.example.test/oauth/callback?tenant=1"
STATE = "state-0123456789"
CLIENT_NAME = "Synthetic Connector"
RESOURCE = f"{ISSUER}/mcp"


def pkce_pair() -> tuple[str, str]:
    """`(code_verifier, code_challenge)` dla S256 (RFC 7636 4.2)."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def register(client: TestClient, *, redirect_uris: list[str] | None = None, name: str | None = CLIENT_NAME,
             auth_method: str | None = "none", scope: str | None = None, **extra: Any):
    """`POST /register`; zwraca odpowiedz (201 = `client_id` w JSON)."""
    body: dict[str, Any] = {"redirect_uris": redirect_uris or [REDIRECT], **extra}
    if name is not None:
        body["client_name"] = name
    if auth_method is not None:
        body["token_endpoint_auth_method"] = auth_method
    if scope is not None:
        body["scope"] = scope
    return client.post("/register", json=body)


def register_ok(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    r = register(client, **kwargs)
    assert r.status_code == 201, r.text
    return r.json()


def authorize(client: TestClient, client_id: str, challenge: str | None, *, scope: str | None = None,
              redirect_uri: str | None = REDIRECT, state: str | None = STATE, resource: str | None = RESOURCE,
              method: str | None = "S256"):
    """`GET /authorize` bez podazania za przekierowaniem."""
    params = {"response_type": "code", "client_id": client_id}
    for key, value in (("code_challenge", challenge), ("code_challenge_method", method), ("scope", scope),
                       ("redirect_uri", redirect_uri), ("state", state), ("resource", resource)):
        if value is not None:
            params[key] = value
    return client.get("/authorize", params=params, follow_redirects=False)


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}


def request_id_from(response) -> str:
    """Id zadania z przekierowania `/authorize` na ekran zgody kokpitu."""
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{ISSUER}/oauth/consent?"), location
    return query_of(location)["request"]


def start(client: TestClient, client_id: str, *, scope: str | None = None) -> tuple[str, str]:
    """`/authorize` z PKCE; zwraca `(request_id, code_verifier)`."""
    verifier, challenge = pkce_pair()
    return request_id_from(authorize(client, client_id, challenge, scope=scope)), verifier


def consent_url(request_id: str) -> str:
    return f"{API}/oauth/requests/{request_id}"


def read_request(client: TestClient, settings: Settings, user_id: str, request_id: str):
    return client.get(consent_url(request_id), headers=user_headers(settings, user_id))


def decide(client: TestClient, settings: Settings, user_id: str, request_id: str, *, approve: bool = True,
           org_id: str | None = None, scopes: list[str] | None = None, headers: dict[str, str] | None = None):
    body: dict[str, Any] = {"approve": approve}
    if org_id is not None:
        body["org_id"] = org_id
    if scopes is not None:
        body["scopes"] = scopes
    return client.post(f"{consent_url(request_id)}/decision", json=body,
                       headers=headers if headers is not None else user_headers(settings, user_id))


def code_from(response) -> str:
    assert response.status_code == 200, response.text
    redirect = response.json()["redirect_to"]
    assert redirect.startswith(REDIRECT.split("?")[0]), redirect
    query = query_of(redirect)
    assert query["state"] == STATE and query["tenant"] == "1", query
    return query["code"]


def token(client: TestClient, client_id: str, code: str, verifier: str, *, redirect_uri: str | None = REDIRECT,
          client_secret: str | None = None):
    data = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "client_id": client_id}
    if redirect_uri is not None:
        data["redirect_uri"] = redirect_uri
    if client_secret is not None:
        data["client_secret"] = client_secret
    return client.post("/token", data=data)


def refresh(client: TestClient, client_id: str, refresh_token: str, *, scope: str | None = None,
            client_secret: str | None = None):
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    if scope is not None:
        data["scope"] = scope
    if client_secret is not None:
        data["client_secret"] = client_secret
    return client.post("/token", data=data)


def revoke(client: TestClient, client_id: str, value: str, *, hint: str | None = None,
           client_secret: str | None = None):
    """`POST /revoke`; bez `client_secret` jak klient publiczny wg RFC 7009 (pole nieobecne w formularzu)."""
    data = {"token": value, "client_id": client_id}
    if hint is not None:
        data["token_type_hint"] = hint
    if client_secret is not None:
        data["client_secret"] = client_secret
    return client.post("/revoke", data=data)


@dataclass
class Connection:
    client_id: str
    request_id: str
    code: str
    verifier: str
    access_token: str
    refresh_token: str
    scope: str


def connect(client: TestClient, settings: Settings, user_id: str, org_id: str, *, scope: str | None = None,
            grant: list[str] | None = None, client_id: str | None = None,
            register_scope: str | None = " ".join(("org:read", "org:manage", "members:manage", "keys:manage",
                                                   "uploads:write", "runs:read", "runs:write", "reports:read",
                                                   "usage:read", "audit:read", "webhooks:manage"))) -> Connection:
    """Pelny przeplyw: rejestracja (o ile nie podano `client_id`) -> /authorize -> zgoda -> /token."""
    if client_id is None:
        client_id = register_ok(client, scope=register_scope)["client_id"]
    request_id, verifier = start(client, client_id, scope=scope)
    code = code_from(decide(client, settings, user_id, request_id, org_id=org_id, scopes=grant))
    r = token(client, client_id, code, verifier)
    assert r.status_code == 200, r.text
    body = r.json()
    return Connection(client_id, request_id, code, verifier, body["access_token"], body["refresh_token"],
                      body["scope"])
