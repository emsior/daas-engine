"""Login CSRF magic linku (M-1): link dziala bez dodatkowego kroku tylko w przegladarce, ktora o niego prosila."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control.security import sha256_hex
from tests.v1_helpers import API, get_rows, token_from_outbox

EMAIL = "jan.kowalski@example.test"


def request_link(client: TestClient, email: str = EMAIL):
    r = client.post(f"{API}/auth/magic-link", json={"email": email})
    assert r.status_code == 202, r.text
    return r


def verify(client: TestClient, token: str, **extra):
    return client.post(f"{API}/auth/magic-link/verify", json={"token": token, **extra})


@pytest.fixture()
def second_browser(cloud_client: TestClient):
    """Drugi klient tej samej aplikacji, bez wspolnego jara cookie (inna przegladarka)."""
    with TestClient(cloud_client.app, base_url="https://testserver") as other:
        yield other


# ================================================================ cookie przy prosbie o link
def test_request_sets_a_host_prefixed_httponly_secure_lax_cookie(cloud_client, cloud_settings):
    r = request_link(cloud_client)
    header = next(h for h in r.headers.get_list("set-cookie") if h.startswith("__Host-daas_ml="))
    lowered = header.lower()
    assert "httponly" in lowered and "secure" in lowered and "samesite=lax" in lowered and "path=/" in lowered
    assert "domain=" not in lowered
    assert f"max-age={cloud_settings.daas_magic_link_ttl_minutes * 60}" in lowered
    assert len(cloud_client.cookies.get("__Host-daas_ml")) >= 32


def test_only_the_hash_of_the_nonce_is_stored(cloud_client, cloud_settings):
    request_link(cloud_client)
    nonce = cloud_client.cookies.get("__Host-daas_ml")
    (link,) = get_rows(cloud_settings, models.MagicLink)
    assert link.nonce_hash == sha256_hex(nonce) and nonce not in link.nonce_hash


def test_the_response_body_does_not_contain_the_nonce(cloud_client):
    r = request_link(cloud_client)
    assert cloud_client.cookies.get("__Host-daas_ml") not in r.text


def test_the_same_browser_reuses_its_nonce_so_older_links_keep_working(cloud_client, cloud_settings):
    request_link(cloud_client)
    first = token_from_outbox(cloud_settings, EMAIL)
    nonce = cloud_client.cookies.get("__Host-daas_ml")
    request_link(cloud_client)
    assert cloud_client.cookies.get("__Host-daas_ml") == nonce
    assert verify(cloud_client, first).status_code == 200


def test_cookie_name_without_tls_has_no_host_prefix(cloud_factory):
    client, settings = cloud_factory(DAAS_COOKIE_SECURE="false")
    r = request_link(client)
    header = next(h for h in r.headers.get_list("set-cookie") if h.startswith("daas_ml="))
    assert "secure" not in header.lower() and "httponly" in header.lower()
    assert verify(client, token_from_outbox(settings, EMAIL)).status_code == 200


# ================================================================ weryfikacja
def test_same_browser_signs_in_without_confirmation(cloud_client, cloud_settings):
    request_link(cloud_client)
    r = verify(cloud_client, token_from_outbox(cloud_settings, EMAIL))
    assert r.status_code == 200, r.text
    assert cloud_client.cookies.get("daas_session")
    assert "__Host-daas_ml" not in cloud_client.cookies or cloud_client.cookies.get("__Host-daas_ml") in ("", None)


def test_another_browser_needs_confirmation_and_the_token_is_not_consumed(cloud_client, cloud_settings,
                                                                          second_browser):
    request_link(cloud_client)
    token = token_from_outbox(cloud_settings, EMAIL)
    r = verify(second_browser, token)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "confirm_required" and r.headers["content-type"].startswith("application/problem+json")
    assert body["email_hint"] == "j***@example.test" and EMAIL not in r.text
    assert not second_browser.cookies.get("daas_session") and "set-cookie" not in r.headers
    (link,) = get_rows(cloud_settings, models.MagicLink)
    assert link.used_at is None
    # prawowity wlasciciel linku nadal moze go uzyc w swojej przegladarce
    assert verify(cloud_client, token).status_code == 200


def test_explicit_confirmation_signs_in_the_other_browser(cloud_client, cloud_settings, second_browser):
    request_link(cloud_client)
    token = token_from_outbox(cloud_settings, EMAIL)
    r = verify(second_browser, token, confirm=True)
    assert r.status_code == 200, r.text
    assert second_browser.cookies.get("daas_session") and r.json()["user"]["email"] == EMAIL
    assert not cloud_client.cookies.get("daas_session")


def test_wrong_cookie_value_needs_confirmation(cloud_client, cloud_settings, second_browser):
    request_link(cloud_client)
    token = token_from_outbox(cloud_settings, EMAIL)
    second_browser.cookies.set("__Host-daas_ml", "x" * 43, domain="testserver.local")
    second_browser.cookies.set("__Host-daas_ml", "x" * 43)
    assert verify(second_browser, token).status_code == 409
    assert verify(second_browser, token, confirm=True).status_code == 200


def test_confirm_false_is_the_same_as_absent(cloud_client, cloud_settings, second_browser):
    request_link(cloud_client)
    assert verify(second_browser, token_from_outbox(cloud_settings, EMAIL), confirm=False).status_code == 409


def test_token_stays_single_use_with_and_without_confirmation(cloud_client, cloud_settings, second_browser):
    request_link(cloud_client)
    token = token_from_outbox(cloud_settings, EMAIL)
    assert verify(cloud_client, token).status_code == 200
    assert verify(cloud_client, token).status_code == 401
    assert verify(second_browser, token, confirm=True).status_code == 401
    assert verify(second_browser, token).status_code == 401


def test_confirm_does_not_make_unknown_or_expired_tokens_valid(cloud_client, cloud_settings, second_browser):
    assert verify(second_browser, "n" * 43, confirm=True).status_code == 401
    request_link(cloud_client)
    token = token_from_outbox(cloud_settings, EMAIL)
    from datetime import timedelta

    from app.control.types import utcnow
    from tests.v1_helpers import update_rows

    update_rows(cloud_settings, models.MagicLink, {"expires_at": utcnow() - timedelta(minutes=1)},
                models.MagicLink.token_hash == sha256_hex(token))
    assert verify(cloud_client, token, confirm=True).status_code == 401
    assert verify(second_browser, token).status_code == 401


def test_a_link_issued_before_the_nonce_existed_requires_confirmation(cloud_client, cloud_settings):
    from tests.v1_helpers import update_rows

    request_link(cloud_client)
    token = token_from_outbox(cloud_settings, EMAIL)
    update_rows(cloud_settings, models.MagicLink, {"nonce_hash": None}, models.MagicLink.token_hash == sha256_hex(token))
    assert verify(cloud_client, token).status_code == 409
    assert verify(cloud_client, token, confirm=True).status_code == 200


def test_attacker_link_planted_in_the_victims_browser_does_not_sign_the_victim_in(cloud_client, cloud_settings,
                                                                                  second_browser):
    """Login CSRF: napastnik prosi o link dla SIEBIE w swojej przegladarce i podsyla ofierze URL z tokenem."""
    request_link(second_browser, "attacker@example.test")             # przegladarka napastnika
    attacker_token = token_from_outbox(cloud_settings, "attacker@example.test")
    victim = verify(cloud_client, attacker_token)                      # przegladarka ofiary klika link
    assert victim.status_code == 409 and victim.json()["code"] == "confirm_required"
    assert not cloud_client.cookies.get("daas_session")


@pytest.mark.parametrize(("email", "hint"), [
    ("jan.kowalski@example.test", "j***@example.test"), ("a@b.pl", "a***@b.pl"), ("Zed@Corp.example", "z***@corp.example"),
])
def test_email_hint_masks_the_local_part(email, hint):
    from app.api.v1.auth import mask_email

    assert mask_email(email) == hint

