"""Uwierzytelnianie API v1: klucze per organizacja, sesja cookie + CSRF, magic link, limity."""
from __future__ import annotations

import re
import smtplib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control import scopes as sc
from app.control.mailer import ConsoleMailer, OutgoingMail, SmtpMailer, build_mailer
from app.control.security import sha256_hex, sign_session_id
from app.control.types import utcnow
from app.core.config import Settings, reset_settings_cache
from app.main import create_app
from tests.conftest import TEST_API_KEY, TEST_SECRET_KEY
from tests.support import assert_private_file
from tests.v1_helpers import (
    API,
    csrf_header,
    get_rows,
    key_headers,
    login_via_magic_link,
    outbox,
    seed_key,
    seed_member,
    seed_org,
    seed_session,
    token_from_outbox,
    update_rows,
    with_db,
)

ME = f"{API}/me"


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


# ================================================================ klucze API
def test_valid_key_identifies_its_org_and_scopes(cloud_client: TestClient, org):
    body = cloud_client.get(ME, headers=key_headers(org.api_key)).json()
    assert body["principal"]["type"] == "api_key" and body["principal"]["org_id"] == org.org_id
    assert set(body["principal"]["scopes"]) == set(sc.KEY_SCOPES) and body["user"] is None
    assert body["orgs"][0]["org_id"] == org.org_id and body["orgs"][0]["role"] is None


def test_key_is_stored_only_as_hash(cloud_settings: Settings, org):
    (row,) = get_rows(cloud_settings, models.ApiKey)
    assert row.key_hash == sha256_hex(org.api_key) and row.prefix == org.api_key[5:13]
    assert org.api_key not in str(row.__dict__)
    assert org.api_key.encode() not in (cloud_settings.runtime_dir.parent / "control.db").read_bytes()


def test_wrong_secret_with_valid_prefix_is_401(cloud_client: TestClient, org):
    forged = org.api_key[:14] + "A" * (len(org.api_key) - 14)
    assert cloud_client.get(ME, headers=key_headers(forged)).status_code == 401


def test_unknown_prefix_is_401(cloud_client: TestClient, org):
    assert cloud_client.get(ME, headers=key_headers("daas_00000000_" + "a" * 43)).status_code == 401


@pytest.mark.parametrize("bad", ["", " ", "x", "daas_", "Bearer abc", "daas_zzzzzzzz_" + "a" * 43, "a" * 400])
def test_malformed_keys_are_401(cloud_client: TestClient, bad: str):
    assert cloud_client.get(ME, headers=key_headers(bad)).status_code == 401


def test_missing_credentials_is_401_problem_json(cloud_client: TestClient):
    r = cloud_client.get(ME)
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 401 and body["code"] == "unauthorized" and body["instance"] == ME
    assert body["request_id"] == r.headers["x-request-id"] and body["type"] == "urn:daas:problem:unauthorized"
    assert r.headers["www-authenticate"] == "ApiKey"


def test_revoked_key_is_401(cloud_client: TestClient, cloud_settings: Settings, org):
    key_id, raw = seed_key(cloud_settings, org.org_id, revoked=True)
    assert cloud_client.get(ME, headers=key_headers(raw)).status_code == 401
    assert cloud_client.get(ME, headers=key_headers(org.api_key)).status_code == 200


def test_expired_key_is_401(cloud_client: TestClient, cloud_settings: Settings, org):
    _, raw = seed_key(cloud_settings, org.org_id, expires_in=timedelta(seconds=-5))
    assert cloud_client.get(ME, headers=key_headers(raw)).status_code == 401


def test_not_yet_expired_key_works(cloud_client: TestClient, cloud_settings: Settings, org):
    _, raw = seed_key(cloud_settings, org.org_id, expires_in=timedelta(days=1))
    assert cloud_client.get(ME, headers=key_headers(raw)).status_code == 200


def test_key_expiring_between_calls_stops_working(cloud_client: TestClient, cloud_settings: Settings, org):
    key_id, raw = seed_key(cloud_settings, org.org_id, expires_in=timedelta(days=1))
    assert cloud_client.get(ME, headers=key_headers(raw)).status_code == 200
    update_rows(cloud_settings, models.ApiKey, {"expires_at": utcnow() - timedelta(seconds=1)},
                models.ApiKey.id == key_id)
    assert cloud_client.get(ME, headers=key_headers(raw)).status_code == 401


def test_revoking_takes_effect_immediately(cloud_client: TestClient, cloud_settings: Settings, org):
    key_id, raw = seed_key(cloud_settings, org.org_id)
    assert cloud_client.get(ME, headers=key_headers(raw)).status_code == 200
    update_rows(cloud_settings, models.ApiKey, {"revoked_at": utcnow()}, models.ApiKey.id == key_id)
    assert cloud_client.get(ME, headers=key_headers(raw)).status_code == 401


def test_rotation_new_key_works_old_key_revoked(cloud_client: TestClient, cloud_settings: Settings, org):
    old_id, old = seed_key(cloud_settings, org.org_id, name="old")
    _, new = seed_key(cloud_settings, org.org_id, name="new")
    update_rows(cloud_settings, models.ApiKey, {"revoked_at": utcnow()}, models.ApiKey.id == old_id)
    assert cloud_client.get(ME, headers=key_headers(old)).status_code == 401
    assert cloud_client.get(ME, headers=key_headers(new)).status_code == 200


def test_last_used_at_is_set_and_throttled(cloud_client: TestClient, cloud_settings: Settings, org):
    key_id, raw = seed_key(cloud_settings, org.org_id)
    assert get_rows(cloud_settings, models.ApiKey, models.ApiKey.id == key_id)[0].last_used_at is None
    cloud_client.get(ME, headers=key_headers(raw))
    first = get_rows(cloud_settings, models.ApiKey, models.ApiKey.id == key_id)[0].last_used_at
    assert first is not None
    cloud_client.get(ME, headers=key_headers(raw))
    assert get_rows(cloud_settings, models.ApiKey, models.ApiKey.id == key_id)[0].last_used_at == first  # throttling
    update_rows(cloud_settings, models.ApiKey, {"last_used_at": utcnow() - timedelta(minutes=10)},
                models.ApiKey.id == key_id)
    cloud_client.get(ME, headers=key_headers(raw))
    assert get_rows(cloud_settings, models.ApiKey, models.ApiKey.id == key_id)[0].last_used_at > first - timedelta(
        minutes=1)
    assert utcnow() - get_rows(cloud_settings, models.ApiKey, models.ApiKey.id == key_id)[0].last_used_at < timedelta(
        minutes=1)


def test_revoked_key_use_is_audited_without_the_key(cloud_client: TestClient, cloud_settings: Settings, org):
    _, raw = seed_key(cloud_settings, org.org_id, revoked=True)
    cloud_client.get(ME, headers=key_headers(raw))
    (event,) = get_rows(cloud_settings, models.AuditEvent, models.AuditEvent.action == "auth_denied")
    assert event.meta == {"reason": "key_revoked"} and raw not in str(event.__dict__)


def test_key_takes_precedence_over_cookie(cloud_client: TestClient, cloud_settings: Settings, org):
    cookie, _ = seed_session(cloud_settings, seed_member(cloud_settings, org.org_id, "m@example.test", "member"))
    cloud_client.cookies.set("daas_session", cookie)
    body = cloud_client.get(ME, headers=key_headers(org.api_key)).json()
    assert body["principal"]["type"] == "api_key"


def test_failed_keys_are_throttled_per_ip(cloud_client: TestClient):
    codes = [cloud_client.get(ME, headers=key_headers("daas_00000000_" + "b" * 43)).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]


def test_valid_key_is_not_blocked_by_failures(cloud_client: TestClient, org):
    for _ in range(12):
        cloud_client.get(ME, headers=key_headers("daas_00000000_" + "b" * 43))
    assert cloud_client.get(ME, headers=key_headers(org.api_key)).status_code == 200


def test_key_comparison_uses_constant_time_helper(cloud_client: TestClient, org, monkeypatch: pytest.MonkeyPatch):
    import app.api.v1.deps as deps

    calls: list[tuple[str, str]] = []
    real = deps.safe_equal
    monkeypatch.setattr(deps, "safe_equal", lambda a, b: calls.append((a, b)) or real(a, b))
    cloud_client.get(ME, headers=key_headers("daas_00000000_" + "c" * 43))  # nieznany prefiks
    cloud_client.get(ME, headers=key_headers(org.api_key))
    assert len(calls) == 2 and all(len(a) == len(b) == 64 for a, b in calls)


def test_selfhost_env_key_authenticates_default_org(client: TestClient):
    body = client.get(ME).json()
    assert body["principal"]["type"] == "api_key" and body["principal"]["org_id"] == "default"
    assert set(body["principal"]["scopes"]) == set(sc.KEY_SCOPES)


def test_selfhost_wrong_env_key_is_401(anon_client: TestClient):
    assert anon_client.get(ME, headers=key_headers(TEST_API_KEY + "x")).status_code == 401


def test_cloud_ignores_env_key(cloud_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DAAS_API_KEY", TEST_API_KEY)
    app = create_app(Settings(_env_file=None))
    with TestClient(app, base_url="https://testserver") as c:
        assert c.get(ME, headers=key_headers(TEST_API_KEY)).status_code == 401


# ================================================================ magic link
def test_magic_link_request_writes_outbox_and_stores_only_the_hash(cloud_client: TestClient,
                                                                    cloud_settings: Settings):
    r = cloud_client.post(f"{API}/auth/magic-link", json={"email": "Jan@Example.TEST"})
    assert r.status_code == 202 and r.json() == {"status": "sent", "expires_in_minutes": 15}
    (mail,) = outbox(cloud_settings, "jan@example.test")
    token = token_from_outbox(cloud_settings, "jan@example.test")
    assert mail["link"].startswith("https://cockpit.example.test/login/verify?token=") and token in mail["body"]
    (row,) = get_rows(cloud_settings, models.MagicLink)
    assert row.token_hash == sha256_hex(token) and row.email == "jan@example.test" and row.used_at is None
    assert token.encode() not in (cloud_settings.runtime_dir.parent / "control.db").read_bytes()
    assert timedelta(minutes=14) < row.expires_at - utcnow() <= timedelta(minutes=15)


def test_outbox_file_is_private(cloud_client: TestClient, cloud_settings: Settings):
    cloud_client.post(f"{API}/auth/magic-link", json={"email": "p@example.test"})
    (path,) = cloud_settings.outbox_dir.glob("*.json")
    assert_private_file(path)


def test_token_has_at_least_32_bytes_of_entropy(cloud_client: TestClient, cloud_settings: Settings):
    cloud_client.post(f"{API}/auth/magic-link", json={"email": "e@example.test"})
    assert len(token_from_outbox(cloud_settings, "e@example.test")) >= 43  # 32 B w base64url


def test_unknown_and_known_addresses_get_identical_answers(cloud_client: TestClient, cloud_settings: Settings, org):
    a = cloud_client.post(f"{API}/auth/magic-link", json={"email": org.email})
    b = cloud_client.post(f"{API}/auth/magic-link", json={"email": "nobody@example.test"})
    assert a.status_code == b.status_code == 202 and a.json() == b.json()


@pytest.mark.parametrize("email", ["", "plainaddress", "a@b", "a b@example.test", "x" * 300 + "@example.test", None, 7])
def test_invalid_email_is_422_without_echo(cloud_client: TestClient, email):
    r = cloud_client.post(f"{API}/auth/magic-link", json={"email": email})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    if isinstance(email, str) and email:
        assert email not in r.text


def test_email_rate_limit(cloud_client: TestClient):
    codes = [cloud_client.post(f"{API}/auth/magic-link", json={"email": "same@example.test"}).status_code
             for _ in range(5)]
    assert codes == [202, 202, 202, 429, 429]
    r = cloud_client.post(f"{API}/auth/magic-link", json={"email": "same@example.test"})
    assert int(r.headers["retry-after"]) >= 1 and r.json()["code"] == "rate_limited"
    assert cloud_client.post(f"{API}/auth/magic-link", json={"email": "other@example.test"}).status_code == 202


def test_email_rate_limit_ignores_letter_case(cloud_client: TestClient):
    codes = [cloud_client.post(f"{API}/auth/magic-link", json={"email": e}).status_code
             for e in ("Same@Example.test", "same@example.TEST", "SAME@example.test", "same@example.test")]
    assert codes == [202, 202, 202, 429]


def test_ip_rate_limit(cloud_client: TestClient):
    codes = [cloud_client.post(f"{API}/auth/magic-link", json={"email": f"u{i}@example.test"}).status_code
             for i in range(12)]
    assert codes[:10] == [202] * 10 and codes[10:] == [429, 429]


def test_magic_link_unavailable_without_secret_key(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DAAS_SECRET_KEY")
    reset_settings_cache()
    with TestClient(create_app(), base_url="https://testserver") as c:
        r = c.post(f"{API}/auth/magic-link", json={"email": "a@example.test"})
        assert r.status_code == 503 and r.json()["code"] == "sessions_unavailable"
        assert c.post(f"{API}/auth/magic-link/verify", json={"token": "t" * 30}).status_code == 503


def test_verify_creates_user_and_session_cookies(cloud_client: TestClient, cloud_settings: Settings):
    body = login_via_magic_link(cloud_client, cloud_settings, "new@example.test")
    assert body["user"]["email"] == "new@example.test" and body["orgs"] == []
    assert body["csrf_token"] == cloud_client.cookies.get("daas_csrf")
    (user,) = get_rows(cloud_settings, models.User)
    assert user.email == "new@example.test"
    me = cloud_client.get(ME).json()
    assert me["principal"]["type"] == "user" and me["user"]["id"] == user.id


def test_session_cookie_attributes(cloud_client: TestClient, cloud_settings: Settings):
    cloud_client.post(f"{API}/auth/magic-link", json={"email": "c@example.test"})
    r = cloud_client.post(f"{API}/auth/magic-link/verify",
                          json={"token": token_from_outbox(cloud_settings, "c@example.test")})
    cookies = {c.split("=", 1)[0]: c for c in r.headers.get_list("set-cookie")}
    session, csrf = cookies["daas_session"].lower(), cookies["daas_csrf"].lower()
    assert "httponly" in session and "secure" in session and "samesite=lax" in session and "path=/" in session
    assert "httponly" not in csrf and "secure" in csrf and "samesite=lax" in csrf
    assert "max-age=1209600" in session


def test_cookie_secure_flag_can_be_disabled_for_local_dev(cloud_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DAAS_COOKIE_SECURE", "false")
    with TestClient(create_app(Settings(_env_file=None))) as c:
        c.post(f"{API}/auth/magic-link", json={"email": "d@example.test"})
        r = c.post(f"{API}/auth/magic-link/verify", json={"token": token_from_outbox(cloud_settings, "d@example.test")})
        assert all("secure" not in h.lower() for h in r.headers.get_list("set-cookie"))


def test_session_row_matches_cookie(cloud_client: TestClient, cloud_settings: Settings):
    login_via_magic_link(cloud_client, cloud_settings, "s@example.test")
    (session,) = get_rows(cloud_settings, models.Session)
    assert cloud_client.cookies.get("daas_session") == sign_session_id(session.id, TEST_SECRET_KEY)
    assert session.csrf_token == cloud_client.cookies.get("daas_csrf")
    assert timedelta(days=13) < session.expires_at - utcnow() <= timedelta(days=14)


def test_magic_link_is_single_use(cloud_client: TestClient, cloud_settings: Settings):
    cloud_client.post(f"{API}/auth/magic-link", json={"email": "once@example.test"})
    token = token_from_outbox(cloud_settings, "once@example.test")
    assert cloud_client.post(f"{API}/auth/magic-link/verify", json={"token": token}).status_code == 200
    other = TestClient(cloud_client.app, base_url="https://testserver")
    r = other.post(f"{API}/auth/magic-link/verify", json={"token": token})
    assert r.status_code == 401 and r.json()["code"] == "invalid_token"
    assert len(get_rows(cloud_settings, models.Session)) == 1


def test_expired_magic_link_is_rejected(cloud_client: TestClient, cloud_settings: Settings):
    cloud_client.post(f"{API}/auth/magic-link", json={"email": "late@example.test"})
    token = token_from_outbox(cloud_settings, "late@example.test")
    update_rows(cloud_settings, models.MagicLink, {"expires_at": utcnow() - timedelta(seconds=1)},
                models.MagicLink.token_hash == sha256_hex(token))
    r = cloud_client.post(f"{API}/auth/magic-link/verify", json={"token": token})
    assert r.status_code == 401 and not get_rows(cloud_settings, models.Session)
    assert get_rows(cloud_settings, models.MagicLink)[0].used_at is None


def test_unknown_token_is_rejected(cloud_client: TestClient):
    r = cloud_client.post(f"{API}/auth/magic-link/verify", json={"token": "n" * 43})
    assert r.status_code == 401 and r.json()["code"] == "invalid_token"


def test_short_token_is_422(cloud_client: TestClient):
    assert cloud_client.post(f"{API}/auth/magic-link/verify", json={"token": "short"}).status_code == 422


def test_failed_verifications_are_throttled(cloud_client: TestClient):
    codes = [cloud_client.post(f"{API}/auth/magic-link/verify", json={"token": f"{i:0>43}"}).status_code
             for i in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]


def test_verify_is_atomic_under_concurrency(cloud_client: TestClient, cloud_settings: Settings):
    from concurrent.futures import ThreadPoolExecutor

    cloud_client.post(f"{API}/auth/magic-link", json={"email": "race@example.test"})
    token = token_from_outbox(cloud_settings, "race@example.test")
    clients = [TestClient(cloud_client.app, base_url="https://testserver") for _ in range(6)]
    with ThreadPoolExecutor(6) as pool:
        codes = list(pool.map(lambda c: c.post(f"{API}/auth/magic-link/verify",
                                               json={"token": token, "confirm": True}).status_code, clients))
    assert sorted(codes) == [200] + [401] * 5


def test_repeat_login_reuses_the_user(cloud_client: TestClient, cloud_settings: Settings):
    login_via_magic_link(cloud_client, cloud_settings, "again@example.test")
    login_via_magic_link(TestClient(cloud_client.app, base_url="https://testserver"), cloud_settings,
                         "AGAIN@example.test")
    assert len(get_rows(cloud_settings, models.User)) == 1 and len(get_rows(cloud_settings, models.Session)) == 2


def test_login_lists_memberships_and_picks_first_org(cloud_client: TestClient, cloud_settings: Settings, org):
    body = login_via_magic_link(cloud_client, cloud_settings, org.email)
    assert [(o["org_id"], o["role"]) for o in body["orgs"]] == [(org.org_id, "owner")]
    (session,) = get_rows(cloud_settings, models.Session)
    assert session.org_id == org.org_id


def test_login_accepts_pending_invitation(cloud_client: TestClient, cloud_settings: Settings, org):
    with_db(cloud_settings, lambda db: _add(db, models.Invitation(
        org_id=org.org_id, email="invited@example.test", role="admin", token_hash="h" * 64,
        expires_at=utcnow() + timedelta(days=7))))
    body = login_via_magic_link(cloud_client, cloud_settings, "invited@example.test")
    assert [(o["org_id"], o["role"]) for o in body["orgs"]] == [(org.org_id, "admin")]
    assert get_rows(cloud_settings, models.Invitation)[0].accepted_at is not None


def test_expired_invitation_is_not_accepted(cloud_client: TestClient, cloud_settings: Settings, org):
    with_db(cloud_settings, lambda db: _add(db, models.Invitation(
        org_id=org.org_id, email="late@example.test", role="admin", token_hash="h" * 64,
        expires_at=utcnow() - timedelta(days=1))))
    assert login_via_magic_link(cloud_client, cloud_settings, "late@example.test")["orgs"] == []


async def _add(db, obj):
    db.add(obj)


def test_me_accepts_invitation_created_after_login(cloud_client: TestClient, cloud_settings: Settings, org):
    login_via_magic_link(cloud_client, cloud_settings, "later@example.test")
    assert cloud_client.get(ME).json()["orgs"] == []
    with_db(cloud_settings, lambda db: _add(db, models.Invitation(
        org_id=org.org_id, email="later@example.test", role="member", token_hash="h" * 64,
        expires_at=utcnow() + timedelta(days=7))))
    assert [o["role"] for o in cloud_client.get(ME).json()["orgs"]] == ["member"]


def test_login_is_audited_in_the_org(cloud_client: TestClient, cloud_settings: Settings, org):
    login_via_magic_link(cloud_client, cloud_settings, org.email)
    (event,) = get_rows(cloud_settings, models.AuditEvent, models.AuditEvent.action == "auth.login")
    assert event.org_id == org.org_id and event.actor_type == "user" and event.actor_id == org.user_id


def test_email_failure_still_answers_202_and_logs(cloud_client: TestClient, monkeypatch: pytest.MonkeyPatch,
                                                   caplog: pytest.LogCaptureFixture):
    def boom(_mail):
        raise OSError("smtp down")

    monkeypatch.setattr(cloud_client.app.state.mailer, "send", boom)
    r = cloud_client.post(f"{API}/auth/magic-link", json={"email": "x@example.test"})
    assert r.status_code == 202 and any("magic link e-mail failed" in x.getMessage() for x in caplog.records)


def test_locale_selects_language_of_the_message(cloud_client: TestClient, cloud_settings: Settings):
    cloud_client.post(f"{API}/auth/magic-link", json={"email": "pl@example.test"})
    cloud_client.post(f"{API}/auth/magic-link", json={"email": "en@example.test", "locale": "en"})
    assert outbox(cloud_settings, "pl@example.test")[0]["subject"].startswith("Link do logowania")
    assert outbox(cloud_settings, "en@example.test")[0]["subject"].startswith("Your DaaS Engine")


# ================================================================ sesja i CSRF
@pytest.fixture()
def session_app(cloud_client: TestClient, cloud_settings: Settings, org):
    """Zalogowany wlasciciel organizacji (real flow) w tym samym kliencie."""
    login_via_magic_link(cloud_client, cloud_settings, org.email)
    return cloud_client


def test_logout_requires_csrf(session_app: TestClient):
    r = session_app.post(f"{API}/auth/logout")
    assert r.status_code == 403 and r.json()["code"] == "csrf_failed"
    assert session_app.get(ME).status_code == 200


def test_logout_with_csrf_ends_the_session(session_app: TestClient, cloud_settings: Settings):
    r = session_app.post(f"{API}/auth/logout", headers=csrf_header(session_app))
    assert r.status_code == 204 and not get_rows(cloud_settings, models.Session)
    cleared = " ".join(r.headers.get_list("set-cookie")).lower()
    assert "daas_session=" in cleared and "max-age=0" in cleared
    session_app.cookies.clear()
    assert session_app.get(ME).status_code == 401


def test_old_cookie_is_useless_after_logout(session_app: TestClient):
    cookie = session_app.cookies.get("daas_session")
    csrf = session_app.cookies.get("daas_csrf")
    session_app.post(f"{API}/auth/logout", headers={"X-CSRF-Token": csrf})
    replay = TestClient(session_app.app, base_url="https://testserver")
    replay.cookies.set("daas_session", cookie)
    assert replay.get(ME).status_code == 401


def test_csrf_wrong_header_is_rejected(session_app: TestClient):
    r = session_app.post(f"{API}/auth/logout", headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403 and r.json()["code"] == "csrf_failed"


def test_csrf_header_without_matching_cookie_is_rejected(session_app: TestClient):
    token = session_app.cookies.get("daas_csrf")
    session_app.cookies.delete("daas_csrf")
    assert session_app.post(f"{API}/auth/logout", headers={"X-CSRF-Token": token}).status_code == 403


def test_csrf_forged_double_submit_is_rejected(session_app: TestClient):
    """Atakujacy ustawia WLASNE cookie i naglowek (rowne sobie), ale nie zna tokenu zapisanego przy sesji."""
    session_app.cookies.set("daas_csrf", "attacker-chosen")
    assert session_app.post(f"{API}/auth/logout", headers={"X-CSRF-Token": "attacker-chosen"}).status_code == 403


def test_safe_methods_do_not_need_csrf(session_app: TestClient):
    assert session_app.get(ME).status_code == 200


def test_api_key_requests_do_not_need_csrf(cloud_client: TestClient, org):
    r = cloud_client.post(f"{API}/auth/logout", headers=key_headers(org.api_key))
    assert r.status_code == 403 and r.json()["code"] == "user_session_required"  # nie csrf_failed


def test_tampered_cookie_is_401(session_app: TestClient):
    cookie = session_app.cookies.get("daas_session")
    session_app.cookies.set("daas_session", cookie[:-2] + ("aa" if not cookie.endswith("aa") else "bb"))
    assert session_app.get(ME).status_code == 401


def test_cookie_signed_with_another_secret_is_401(cloud_client: TestClient, cloud_settings: Settings, org):
    cookie, _ = seed_session(cloud_settings, org.user_id)
    session_id = cookie.rpartition(".")[0]
    cloud_client.cookies.set("daas_session", sign_session_id(session_id, "z" * 40))
    assert cloud_client.get(ME).status_code == 401


def test_unsigned_session_id_is_401(cloud_client: TestClient, cloud_settings: Settings, org):
    cookie, _ = seed_session(cloud_settings, org.user_id)
    cloud_client.cookies.set("daas_session", cookie.rpartition(".")[0])
    assert cloud_client.get(ME).status_code == 401


def test_expired_session_is_401(cloud_client: TestClient, cloud_settings: Settings, org):
    cookie, _ = seed_session(cloud_settings, org.user_id, ttl=timedelta(seconds=-1))
    cloud_client.cookies.set("daas_session", cookie)
    assert cloud_client.get(ME).status_code == 401


def test_valid_seeded_session_works(cloud_client: TestClient, cloud_settings: Settings, org):
    cookie, _ = seed_session(cloud_settings, org.user_id)
    cloud_client.cookies.set("daas_session", cookie)
    me = cloud_client.get(ME).json()
    assert me["user"]["email"] == org.email and me["orgs"][0]["role"] == "owner"


def test_deleting_user_invalidates_session(cloud_client: TestClient, cloud_settings: Settings, org):
    cookie, _ = seed_session(cloud_settings, org.user_id)
    cloud_client.cookies.set("daas_session", cookie)

    async def drop(db):
        await db.delete(await db.get(models.User, org.user_id))

    with_db(cloud_settings, drop)
    assert cloud_client.get(ME).status_code == 401  # kaskada usunela sesje


# ================================================================ adaptery e-mail
def test_console_mailer_writes_outbox(tmp_path):
    ConsoleMailer(tmp_path / "ob").send(OutgoingMail(to="a@example.test", subject="s", body="b", link="https://x"))
    (f,) = (tmp_path / "ob").glob("*.json")
    assert '"link": "https://x"' in f.read_text()


def test_build_mailer_selects_backend(settings: Settings):
    assert isinstance(build_mailer(settings), ConsoleMailer)
    smtp = Settings(_env_file=None, daas_email_backend="smtp", daas_smtp_host="smtp.example.test")
    assert isinstance(build_mailer(smtp), SmtpMailer)


def test_smtp_backend_requires_host():
    with pytest.raises(RuntimeError, match="DAAS_SMTP_HOST"):
        build_mailer(Settings(_env_file=None, daas_email_backend="smtp"))


def test_smtp_mailer_uses_starttls_login_and_sends(monkeypatch: pytest.MonkeyPatch):
    events: list = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            events.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            events.append(("starttls", context is not None))

        def login(self, user, password):
            events.append(("login", user, password))

        def send_message(self, msg):
            events.append(("send", msg["To"], msg["Subject"], msg["From"]))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    s = Settings(_env_file=None, daas_email_backend="smtp", daas_smtp_host="smtp.example.test", daas_smtp_port=2525,
                 daas_smtp_user="u", daas_smtp_password="p", daas_email_from="DaaS <no-reply@example.test>")
    SmtpMailer(s).send(OutgoingMail(to="a@example.test", subject="Hi", body="b"))
    assert events == [("connect", "smtp.example.test", 2525), ("starttls", True), ("login", "u", "p"),
                      ("send", "a@example.test", "Hi", "DaaS <no-reply@example.test>")]


def test_smtp_mailer_without_starttls_or_login(monkeypatch: pytest.MonkeyPatch):
    seen: list = []

    class FakeSMTP:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **k): seen.append("starttls")
        def login(self, *a): seen.append("login")
        def send_message(self, msg): seen.append("send")

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    s = Settings(_env_file=None, daas_email_backend="smtp", daas_smtp_host="h", daas_smtp_starttls=False)
    SmtpMailer(s).send(OutgoingMail(to="a@example.test", subject="s", body="b"))
    assert seen == ["send"]


def test_magic_link_url_uses_cockpit_origin(cloud_settings: Settings):
    from app.control.mailer import magic_link_mail

    mail = magic_link_mail(cloud_settings, "a@example.test", "TOKEN", "pl")
    assert mail.link == "https://cockpit.example.test/login/verify?token=TOKEN"
    assert re.search(r"15 min", mail.body)
