"""Brama G2: dwie organizacje x KAZDA trasa z `{org_id}`.

Lista tras jest brana z `app.routes` (jak w `test_auth.py`), wiec nowa trasa org-scoped jest objeta automatycznie.
Konwencja odpowiedzi (jedna, sprawdzana scisle):
    * zasob obcej organizacji -> `404` (nie ujawniamy istnienia),
    * trasy `members` i `invitations` -> `403` (decyzja kontraktu v1),
    * identyfikator zasobu organizacji B uzyty w sciezce organizacji A -> `404`.
Zadna z tych prob nie moze zwrocic 2xx ani zmienic stanu organizacji B (test na koncu pliku).
Szczegolowe scenariusze (audyt, SSE, raport, verify, uploady krzyzowe) maja swoje testy w `test_usage_audit.py`,
`test_jobs.py`, `test_verify.py`; tu jest lapacz wszystkich tras plus kilka kontroli krzyzowych na wspolnym swiecie.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control import scopes as sc
from app.control.ids import new_ulid
from app.control.types import utcnow
from app.core.config import Settings
from app.main import create_app
from tests.conftest import TEST_SECRET_KEY, iter_api_routes
from tests.v1_helpers import (
    API,
    Seeded,
    get_rows,
    key_headers,
    seed_key,
    seed_member,
    seed_org,
    user_headers,
    with_db,
)

PLACEHOLDER = re.compile(r"\{([^}]+)\}")
#: Parametry sciezki, ktore umiemy wypelnic identyfikatorami zasobow organizacji B. Nowy parametr = rozszerz mape.
RESOURCE_PARAMS = {"job_id", "run_id", "key_id", "webhook_id", "user_id", "invitation_id"}
KNOWN_HASH = "ab" * 32


def _enumerate_routes() -> list[tuple[str, str]]:
    probe = create_app(Settings(_env_file=None, daas_mode="cloud", daas_secret_key=TEST_SECRET_KEY,
                                daas_control_db_url="sqlite+aiosqlite:///:memory:", daas_auto_migrate=False,
                                daas_allow_console_mail=True))
    found = set()
    for route in iter_api_routes(probe):
        if "{org_id}" in route.path:
            for method in route.methods - {"HEAD", "OPTIONS"}:
                found.add((method, route.path))
    return sorted(found, key=lambda x: (x[1], x[0]))


ORG_ROUTES = _enumerate_routes()


def _cases() -> list[tuple[str, str, str]]:
    cases = []
    for method, path in ORG_ROUTES:
        cases += [("key_of_a_on_org_b", method, path), ("non_member_session_on_org_b", method, path)]
        if set(PLACEHOLDER.findall(path)) - {"org_id"}:
            cases.append(("resource_of_b_under_org_a", method, path))
    return cases


CASES = _cases()


@dataclass
class World:
    client: TestClient
    settings: Settings
    a: Seeded
    b: Seeded
    outsider_headers: dict[str, str]
    b_ids: dict[str, str]
    b_upload_id: str


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    root: Path = tmp_path_factory.mktemp("isolation")
    settings = Settings(
        _env_file=None, daas_mode="cloud", daas_secret_key=TEST_SECRET_KEY, daas_api_key=None,
        daas_control_db_url=f"sqlite+aiosqlite:///{root / 'control.db'}",
        duckdb_path=str(root / "runtime" / "daas.duckdb"), reports_dir=str(root / "runtime" / "reports"),
        daas_rate_limit_ip_per_min=0, daas_metrics_token=None, daas_allow_console_mail=True)
    settings.ensure_dirs()
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        a = seed_org(settings, name="Org A", owner_email="a-owner@example.test")
        b = seed_org(settings, name="Org B", owner_email="b-owner@example.test")
        outsider = user_headers(settings, seed_member(settings, a.org_id, "outsider@example.test", "owner"))
        key_id, _ = seed_key(settings, b.org_id, scopes=list(sc.KEY_SCOPES), name="b-extra")
        ids = {"job_id": new_ulid(), "run_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "key_id": key_id,
               "webhook_id": new_ulid(), "user_id": b.user_id, "invitation_id": new_ulid()}
        upload_id = "b" * 32

        async def seed_b(db):
            db.add(models.Job(id=ids["job_id"], org_id=b.org_id, pipeline="cs2_demo", status="queued",
                              force_mock=True))
            db.add(models.RunRecord(run_id=ids["run_id"], org_id=b.org_id, pipeline="cs2_demo", status="SUCCESS",
                                    sha256_html=KNOWN_HASH, sha256_md="cd" * 32, sha256_json="ef" * 32))
            db.add(models.Webhook(id=ids["webhook_id"], org_id=b.org_id, url="https://b.example.test/hook",
                                  secret_hash="0" * 64, events=["run.finished"]))
            db.add(models.Invitation(id=ids["invitation_id"], org_id=b.org_id, email="invited@example.test",
                                     role="member", token_hash="2" * 64,
                                     expires_at=utcnow() + timedelta(days=7)))
            db.add(models.Upload(id=upload_id, org_id=b.org_id, kind="csv", filename="b.csv", bytes=10,
                                 sha256="1" * 64, path=f"{upload_id}.csv",
                                 retention_until=utcnow() + timedelta(days=30)))

        with_db(settings, seed_b)
        yield World(client, settings, a, b, outsider, ids, upload_id)


def _fill(template: str, org_id: str, resources: dict[str, str]) -> str:
    def value(match: re.Match) -> str:
        name = match.group(1)
        if name == "org_id":
            return org_id
        assert name in RESOURCE_PARAMS, (
            f"trasa {template} ma nowy parametr {{{name}}}: dopisz go do RESOURCE_PARAMS i do World.b_ids")
        return resources[name]

    return PLACEHOLDER.sub(value, template)


def _send(w: World, method: str, url: str, headers: dict[str, str]):
    kwargs: dict = {}
    if url.endswith("/uploads"):
        kwargs["files"] = {"file": ("dane.csv", b"a;b\n1;2\n", "text/csv")}
    elif method == "PATCH" and "/members/" in url:
        kwargs["json"] = {"role": "member"}      # poprawne cialo: sprawdzamy autoryzacje, nie walidacje
    elif method == "PUT" and url.endswith("/schedule"):
        kwargs["json"] = {"reminder_enabled": True, "auto_run_on_upload": True}  # poprawne cialo: test autoryzacji
    elif method in ("POST", "PATCH", "PUT"):
        kwargs["json"] = {}
    return w.client.request(method, url, headers=headers, **kwargs)


def _expected(template: str) -> int:
    return 403 if re.search(r"/(members|invitations)(/|$)", template) else 404


def test_inventory_covers_the_whole_org_scoped_surface():
    templates = {path for _, path in ORG_ROUTES}
    assert len(ORG_ROUTES) >= 31
    assert ("GET", "/api/v1/orgs/{org_id}/schedule") in ORG_ROUTES
    assert ("PUT", "/api/v1/orgs/{org_id}/schedule") in ORG_ROUTES
    for needed in ("/api/v1/orgs/{org_id}", "/api/v1/orgs/{org_id}/jobs/{job_id}/events",
                   "/api/v1/orgs/{org_id}/runs/{run_id}/report", "/api/v1/orgs/{org_id}/webhooks/{webhook_id}/ping",
                   "/api/v1/orgs/{org_id}/api-keys/{key_id}", "/api/v1/orgs/{org_id}/audit",
                   "/api/v1/orgs/{org_id}/verify", "/api/v1/orgs/{org_id}/uploads", "/api/v1/orgs/{org_id}/schedule"):
        assert needed in templates, needed


@pytest.mark.parametrize(("variant", "method", "template"), CASES,
                         ids=[f"{v}:{m} {t.removeprefix('/api/v1')}" for v, m, t in CASES])
def test_no_org_route_serves_another_organizations_resources(world: World, variant, method, template):
    if variant == "key_of_a_on_org_b":
        url, headers = _fill(template, world.b.org_id, world.b_ids), key_headers(world.a.api_key)
    elif variant == "non_member_session_on_org_b":
        url, headers = _fill(template, world.b.org_id, world.b_ids), world.outsider_headers
    else:  # identyfikatory zasobow B w sciezce organizacji A, z pelnymi prawami A
        url, headers = _fill(template, world.a.org_id, world.b_ids), key_headers(world.a.api_key)
    r = _send(world, method, url, headers)
    assert not 200 <= r.status_code < 300, f"{method} {url}: {r.status_code} {r.text[:200]}"
    expected = 404 if variant == "resource_of_b_under_org_a" else _expected(template)
    assert r.status_code == expected, f"{method} {url}: {r.status_code} {r.text[:200]}"
    # odpowiedz bledu nie ujawnia niczego o organizacji B (poza sciezka, ktora klient sam podal w `instance`)
    body = r.text.replace(url, "")
    for secret in (world.b.org_id, world.b.email, "b.example.test", *world.b_ids.values()):
        assert secret not in body, secret


# ================================================================ kontrole krzyzowe na wspolnym swiecie
def test_positive_control_the_owner_of_b_reaches_the_same_resources(world: World):
    hdr, b = key_headers(world.b.api_key), world.b
    for path in (f"/orgs/{b.org_id}", f"/orgs/{b.org_id}/jobs/{world.b_ids['job_id']}",
                 f"/orgs/{b.org_id}/runs/{world.b_ids['run_id']}", f"/orgs/{b.org_id}/webhooks",
                 f"/orgs/{b.org_id}/api-keys", f"/orgs/{b.org_id}/usage", f"/orgs/{b.org_id}/audit"):
        r = world.client.get(API + path, headers=hdr)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])


def test_verify_of_b_hash_is_unknown_for_a_and_known_for_b(world: World):
    cross = world.client.post(f"{API}/orgs/{world.a.org_id}/verify", json={"sha256": KNOWN_HASH},
                              headers=key_headers(world.a.api_key))
    assert cross.status_code == 200 and cross.json()["known"] is False
    assert world.b_ids["run_id"] not in cross.text
    own = world.client.post(f"{API}/orgs/{world.b.org_id}/verify", json={"sha256": KNOWN_HASH},
                            headers=key_headers(world.b.api_key))
    assert own.json()["known"] is True and own.json()["run_id"] == world.b_ids["run_id"]


def test_upload_of_b_cannot_be_started_from_a(world: World):
    r = world.client.post(f"{API}/orgs/{world.a.org_id}/jobs", headers=key_headers(world.a.api_key),
                          json={"pipeline": "ecommerce_demo", "upload_id": world.b_upload_id})
    assert r.status_code == 404


def test_audit_and_listings_of_a_never_contain_b_resources(world: World):
    hdr = key_headers(world.a.api_key)
    for suffix in ("jobs", "runs", "webhooks", "api-keys", "audit", "members"):
        r = world.client.get(f"{API}/orgs/{world.a.org_id}/{suffix}", headers=hdr)
        assert r.status_code == 200, (suffix, r.text[:200])
        for secret in (world.b.org_id, *world.b_ids.values(), world.b.email, "b.example.test"):
            assert secret not in r.text, (suffix, secret)


def test_nothing_of_b_changed_after_all_the_attempts(world: World):
    """Uruchamiany po parametryzowanych probach (kolejnosc pliku): zadna nie zmienila stanu organizacji B."""
    (org_b,) = get_rows(world.settings, models.Org, models.Org.id == world.b.org_id)
    assert org_b.name == "Org B" and org_b.retention_days == 90 and org_b.plan == "free"
    keys = {k.id: k for k in get_rows(world.settings, models.ApiKey, models.ApiKey.org_id == world.b.org_id)}
    assert world.b_ids["key_id"] in keys and all(k.revoked_at is None for k in keys.values())
    assert len(get_rows(world.settings, models.Webhook, models.Webhook.org_id == world.b.org_id)) == 1
    (job,) = get_rows(world.settings, models.Job, models.Job.id == world.b_ids["job_id"])
    assert job.status == "queued"
    assert len(get_rows(world.settings, models.Upload, models.Upload.org_id == world.b.org_id)) == 1
    assert [i.id for i in get_rows(world.settings, models.Invitation,
                                   models.Invitation.org_id == world.b.org_id)] == [world.b_ids["invitation_id"]]
    assert get_rows(world.settings, models.OrgSchedule, models.OrgSchedule.org_id == world.b.org_id) == []
    members = get_rows(world.settings, models.Membership, models.Membership.org_id == world.b.org_id)
    assert [m.role for m in members] == ["owner"]
    assert not get_rows(world.settings, models.Membership, models.Membership.user_id.in_(
        [u.id for u in get_rows(world.settings, models.User, models.User.email == "outsider@example.test")]),
        models.Membership.org_id == world.b.org_id)
