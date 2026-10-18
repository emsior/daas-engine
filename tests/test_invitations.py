"""Zaproszenia (M-2/M-3): stala odpowiedz 202, limity 20/org/dobe i 5/min/uzytkownika, sanityzacja nazwy org w mailu."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control.mailer import invitation_mail, sanitize_org_name
from app.core.config import Settings
from tests.v1_helpers import (
    API,
    get_rows,
    key_headers,
    outbox,
    seed_member,
    seed_org,
    update_rows,
    user_headers,
)


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def invite(client: TestClient, org_id: str, n: int | str, headers: dict, role: str = "member"):
    email = f"user{n}@example.test" if isinstance(n, int) else n
    return client.post(f"{API}/orgs/{org_id}/invitations", json={"email": email, "role": role}, headers=headers)


# ================================================================ odpowiedz bez wyroczni
def test_answer_is_identical_for_new_known_and_member_addresses(cloud_client, cloud_settings, org):
    seed_org(cloud_settings, name="Other", owner_email="known@example.test")   # istniejace konto w innej org
    hdr = key_headers(org.api_key)
    answers = [invite(cloud_client, org.org_id, email, hdr) for email in
               ("brand-new@example.test", "known@example.test", org.email)]
    assert {(a.status_code, a.text) for a in answers} == {(202, '{"status":"accepted"}')}
    assert {h for a in answers for h in a.headers if h.lower() == "location"} == set()


def test_mail_is_sent_only_to_non_members(cloud_client, cloud_settings, org):
    hdr = key_headers(org.api_key)
    invite(cloud_client, org.org_id, "brand-new@example.test", hdr)
    invite(cloud_client, org.org_id, org.email, hdr)
    assert len(outbox(cloud_settings, "brand-new@example.test")) == 1
    assert not outbox(cloud_settings, org.email)


# ================================================================ limity
def test_per_user_limit_is_5_per_minute(cloud_client, cloud_settings, org):
    hdr = user_headers(cloud_settings, org.user_id)
    codes = [invite(cloud_client, org.org_id, n, hdr).status_code for n in range(7)]
    assert codes == [202] * 5 + [429] * 2
    blocked = invite(cloud_client, org.org_id, 99, hdr)
    assert blocked.json()["code"] == "invitation_limit" and int(blocked.headers["retry-after"]) >= 1
    assert len(get_rows(cloud_settings, models.Invitation)) == 5


def test_per_user_limit_is_separate_per_principal(cloud_client, cloud_settings, org):
    admin = user_headers(cloud_settings, seed_member(cloud_settings, org.org_id, "adm@example.test", "admin"))
    owner = user_headers(cloud_settings, org.user_id)
    for n in range(5):
        assert invite(cloud_client, org.org_id, n, owner).status_code == 202
    assert invite(cloud_client, org.org_id, 50, owner).status_code == 429
    assert invite(cloud_client, org.org_id, 51, admin).status_code == 202


def test_per_org_daily_limit_is_20(cloud_factory, cloud_settings):
    client, settings = cloud_factory()
    seeded = seed_org(settings)
    hdrs = [key_headers(seeded.api_key)]
    # kilka podmiotow, zeby nie zlapac limitu minutowego (5/min na podmiot)
    from tests.v1_helpers import seed_key

    for _ in range(4):
        hdrs.append(key_headers(seed_key(settings, seeded.org_id)[1]))
    codes = [invite(client, seeded.org_id, n, hdrs[n % 5]).status_code for n in range(22)]
    assert codes[:20] == [202] * 20 and codes[20:] == [429, 429]
    r = invite(client, seeded.org_id, 100, hdrs[0])
    assert r.json()["code"] == "invitation_limit"
    other = seed_org(settings, name="Other", owner_email="other@example.test")
    assert invite(client, other.org_id, 1, key_headers(other.api_key)).status_code == 202   # limit jest per org


def test_daily_limit_window_is_24_hours(cloud_factory):
    from datetime import timedelta

    from app.control.types import utcnow

    client, settings = cloud_factory()
    seeded = seed_org(settings)
    from tests.v1_helpers import seed_key

    keys = [key_headers(seeded.api_key)] + [key_headers(seed_key(settings, seeded.org_id)[1]) for _ in range(4)]
    for n in range(20):
        assert invite(client, seeded.org_id, n, keys[n % 5]).status_code == 202
    assert invite(client, seeded.org_id, 20, keys[0]).status_code == 429
    update_rows(settings, models.AuditEvent, {"ts": utcnow() - timedelta(hours=25)},
                models.AuditEvent.action == "member.invited")
    assert invite(client, seeded.org_id, 21, keys[1]).status_code == 202


def test_validation_errors_do_not_consume_the_limit(cloud_client, cloud_settings, org):
    hdr = user_headers(cloud_settings, org.user_id)
    for _ in range(10):
        assert cloud_client.post(f"{API}/orgs/{org.org_id}/invitations", json={"email": "bad"},
                                 headers=hdr).status_code == 422
    assert invite(cloud_client, org.org_id, 1, hdr).status_code == 202


# ================================================================ tresc maila
@pytest.mark.parametrize(("raw", "expected_absent"), [
    ("Acme https://evil.example/login now", "https://"),
    ("Pay at www.evil.example/x", "www."),
    ("Click evil.example/phish", "evil.example"),
    ("Firma [napisz](mailto:ktos@evil.example) tel:+48000000000", "mailto"),
    ("Acme\nBcc: attacker@example.test", "\n"),
    ("Acme\x00\x1b[31m red", "\x1b"),
    ("<script>alert(1)</script>", "<"),
    ("A‮B​C", "‮"),
])
def test_org_name_in_mail_is_sanitized(raw, expected_absent):
    clean = sanitize_org_name(raw)
    assert expected_absent not in clean and clean == clean.strip() and len(clean) <= 80
    assert "http" not in clean


def test_org_name_is_trimmed_to_80_chars_and_keeps_normal_names():
    assert len(sanitize_org_name("N" * 300)) == 80
    assert sanitize_org_name("Sklep Przykładowy sp. z o.o.") == "Sklep Przykładowy sp. z o.o."
    assert sanitize_org_name("   ") == "organization"


@pytest.mark.parametrize("locale", ["pl", "en"])
def test_subject_is_constant_and_has_no_org_name(locale):
    s = Settings(_env_file=None)
    a = invitation_mail(s, "a@example.test", "Alpha", "member", locale)
    b = invitation_mail(s, "a@example.test", "Beta https://x.example", "member", locale)
    assert a.subject == b.subject and "Alpha" not in a.subject and "Beta" not in b.subject
    assert "https://x.example" not in b.body and "Beta" in b.body


def test_hostile_org_name_end_to_end(cloud_client, cloud_settings, org):
    update_rows(cloud_settings, models.Org, {"name": "Free money https://evil.example/claim\r\nBcc: x@example.test"},
                models.Org.id == org.org_id)
    assert invite(cloud_client, org.org_id, "victim@example.test", key_headers(org.api_key)).status_code == 202
    (mail,) = outbox(cloud_settings, "victim@example.test")
    assert "evil.example" not in mail["body"] + mail["subject"]
    assert "\r" not in mail["subject"] and "Bcc" not in mail["subject"]
    assert mail["body"].count("http") == 1          # tylko link logowania
