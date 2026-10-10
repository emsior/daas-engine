"""Webhooki: zarzadzanie (sekret raz, tylko skrot w bazie), podpis HMAC, ponowienia x3, SSRF, ping, brak danych klienta.

Siec zewnetrzna nie jest uzywana: transport to `httpx.MockTransport`, a rozwiazywanie nazw DNS jest podstawione.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control import scopes as sc
from app.control.security import sha256_hex
from app.core.config import Settings
from tests.v1_helpers import (
    API,
    get_rows,
    key_headers,
    seed_key,
    seed_member,
    seed_org,
    update_rows,
    user_headers,
)

URL = "https://hooks.example.test/daas"
PUBLIC_IP = "93.184.216.34"
RUN_FINISHED_KEYS = {"event", "org_id", "job_id", "run_id", "pipeline", "status", "run_status", "finished_at"}
SECRET_CLIENT_NAME = "Tajna Firma Sp. z o.o."


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


@dataclass
class Sink:
    """Podstawiony odbiorca: zapamietuje zadania i odpowiada kolejnymi statusami z `statuses`."""

    statuses: list[int] = field(default_factory=lambda: [200])
    raises: Exception | None = None
    location: str | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)
    addresses: list[str] = field(default_factory=lambda: [PUBLIC_IP])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        status = self.statuses[min(len(self.requests), len(self.statuses)) - 1]
        headers = {"Location": self.location} if self.location else {}
        return httpx.Response(status, headers=headers, content=b"customer-visible-nothing")


@pytest.fixture()
def sink(cloud_client: TestClient) -> Sink:
    s = Sink()
    dispatcher = cloud_client.app.state.webhooks
    dispatcher.transport = httpx.MockTransport(s.handler)

    async def resolver(host: str, port: int) -> list[str]:
        return list(s.addresses)

    async def sleep(seconds: float) -> None:
        s.sleeps.append(seconds)

    dispatcher.resolver = resolver
    dispatcher.sleep = sleep
    return s


def hooks_url(org_id: str) -> str:
    return f"{API}/orgs/{org_id}/webhooks"


def create(client: TestClient, org, url: str = URL, **body):
    return client.post(hooks_url(org.org_id), json={"url": url, **body}, headers=key_headers(org.api_key))


def wait_for(predicate, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def audit_actions(settings: Settings, org_id: str, prefix: str = "webhook.") -> list[models.AuditEvent]:
    rows = get_rows(settings, models.AuditEvent, models.AuditEvent.org_id == org_id)
    return [e for e in rows if e.action.startswith(prefix)]


def signature_ok(request: httpx.Request, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), request.content, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, request.headers["x-daas-signature"])


# ================================================================ zarzadzanie
def test_create_returns_the_secret_once_and_stores_only_its_hash(cloud_client, cloud_settings, org):
    r = create(cloud_client, org)
    assert r.status_code == 201, r.text
    body = r.json()
    secret = body["secret"]
    assert secret.startswith("whsec_") and len(secret) > 40
    assert body["url"] == URL and body["events"] == ["run.finished"] and body["active"] is True
    (row,) = get_rows(cloud_settings, models.Webhook)
    assert row.secret_hash == sha256_hex(secret)
    assert secret not in json.dumps({c.name: str(getattr(row, c.name)) for c in row.__table__.columns})
    listed = cloud_client.get(hooks_url(org.org_id), headers=key_headers(org.api_key))
    assert listed.status_code == 200 and secret not in listed.text
    assert [h["id"] for h in listed.json()] == [body["id"]] and "secret" not in listed.json()[0]


def test_each_webhook_gets_its_own_secret(cloud_client, cloud_settings, org):
    a = create(cloud_client, org, url="https://a.example.test/x").json()["secret"]
    b = create(cloud_client, org, url="https://b.example.test/x").json()["secret"]
    assert a != b


@pytest.mark.parametrize("url", [
    "http://hooks.example.test/x",           # brak https
    "https://localhost/x", "https://localhost:8443/x", "https://api.localhost/x",
    "https://127.0.0.1/x", "https://127.1.2.3:9000/x", "https://[::1]/x", "https://[::ffff:127.0.0.1]/x",
    "https://10.0.0.5/x", "https://192.168.1.10/x", "https://172.16.0.1/x",
    "https://169.254.169.254/latest/meta-data", "https://100.64.0.1/x", "https://0.0.0.0/x",
    "https://intranet/x", "https://printer.local/x", "https://db.internal/x",
    "https://user:pass@hooks.example.test/x", "ftp://hooks.example.test/x", "javascript:alert(1)",
    "not a url", "https:///nohost", "https://hooks.example.test/a b",
])
def test_create_rejects_unsafe_or_malformed_urls_outside_dev(cloud_client, cloud_settings, org, url):
    r = create(cloud_client, org, url=url)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "webhook_url_invalid"
    assert not get_rows(cloud_settings, models.Webhook)


def test_dev_mode_allows_http_and_loopback(cloud_factory):
    client, settings = cloud_factory(APP_ENV="dev")
    seeded = seed_org(settings)
    with client:
        r = create(client, seeded, url="http://127.0.0.1:9999/hook")
        assert r.status_code == 201, r.text


@pytest.mark.parametrize("events", [["run.started"], ["run.finished", "job.created"], [], ["RUN.FINISHED"]])
def test_create_rejects_unknown_or_empty_events(cloud_client, cloud_settings, org, events):
    assert create(cloud_client, org, events=events).status_code == 422


def test_duplicate_events_are_collapsed(cloud_client, cloud_settings, org):
    r = create(cloud_client, org, events=["run.finished", "run.finished"])
    assert r.status_code == 201 and r.json()["events"] == ["run.finished"]


def test_duplicate_url_is_409(cloud_client, cloud_settings, org):
    assert create(cloud_client, org).status_code == 201
    r = create(cloud_client, org)
    assert r.status_code == 409 and r.json()["code"] == "webhook_exists"


def test_the_same_url_may_exist_in_two_organizations(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    assert create(cloud_client, org).status_code == 201
    assert create(cloud_client, other).status_code == 201


def test_webhook_count_per_org_is_limited(cloud_client, cloud_settings, org):
    for n in range(10):
        assert create(cloud_client, org, url=f"https://h{n}.example.test/x").status_code == 201
    r = create(cloud_client, org, url="https://h10.example.test/x")
    assert r.status_code == 409 and r.json()["code"] == "webhook_limit_reached"


def test_delete_removes_the_webhook_and_is_audited(cloud_client, cloud_settings, org):
    wid = create(cloud_client, org).json()["id"]
    url = f"{hooks_url(org.org_id)}/{wid}"
    assert cloud_client.delete(url, headers=key_headers(org.api_key)).status_code == 204
    assert cloud_client.delete(url, headers=key_headers(org.api_key)).status_code == 404
    assert not get_rows(cloud_settings, models.Webhook)
    assert [e.action for e in audit_actions(cloud_settings, org.org_id)] == ["webhook.created", "webhook.deleted"]


def test_webhooks_are_isolated_between_organizations(cloud_client, cloud_settings, org):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    wid = create(cloud_client, other).json()["id"]
    mine = key_headers(org.api_key)
    assert cloud_client.get(hooks_url(other.org_id), headers=mine).status_code == 404
    assert cloud_client.get(hooks_url(org.org_id), headers=mine).json() == []
    # webhook obcej organizacji pod moja sciezka: 404, nie 403 i nie sukces
    assert cloud_client.delete(f"{hooks_url(org.org_id)}/{wid}", headers=mine).status_code == 404
    assert cloud_client.post(f"{hooks_url(org.org_id)}/{wid}/ping", headers=mine).status_code == 404
    assert len(get_rows(cloud_settings, models.Webhook)) == 1


def test_manage_scope_is_required(cloud_client, cloud_settings, org):
    _, narrow = seed_key(cloud_settings, org.org_id, scopes=[sc.RUNS_READ])
    for method, path in (("get", ""), ("post", ""), ("delete", "/x"), ("post", "/x/ping")):
        r = getattr(cloud_client, method)(hooks_url(org.org_id) + path, headers=key_headers(narrow),
                                          **({"json": {"url": URL}} if method == "post" and not path else {}))
        assert r.status_code == 403 and r.json()["required_scope"] == sc.WEBHOOKS_MANAGE, (method, path)


def test_member_role_cannot_manage_webhooks_admin_can(cloud_client, cloud_settings, org):
    member = user_headers(cloud_settings, seed_member(cloud_settings, org.org_id, "m@example.test", "member"))
    admin = user_headers(cloud_settings, seed_member(cloud_settings, org.org_id, "a@example.test", "admin"))
    assert cloud_client.get(hooks_url(org.org_id), headers=member).status_code == 403
    assert cloud_client.get(hooks_url(org.org_id), headers=admin).status_code == 200


def test_webhooks_require_authentication(cloud_client, org):
    assert cloud_client.get(hooks_url(org.org_id)).status_code == 401


# ================================================================ ping
def test_ping_delivers_a_signed_event(cloud_client, cloud_settings, org, sink):
    created = create(cloud_client, org).json()
    r = cloud_client.post(f"{hooks_url(org.org_id)}/{created['id']}/ping", headers=key_headers(org.api_key))
    assert r.status_code == 200, r.text
    assert r.json() == {"delivered": True, "status": None, "status_class": "2xx"}
    (req,) = sink.requests
    assert req.url.host == PUBLIC_IP and req.headers["host"] == "hooks.example.test" and req.method == "POST"
    assert req.headers["x-daas-event"] == "ping"
    assert len(req.headers["x-daas-delivery"]) == 26
    assert abs(int(req.headers["x-daas-timestamp"]) - time.time()) < 60
    assert req.headers["content-type"] == "application/json"
    assert signature_ok(req, created["secret"])
    assert json.loads(req.content) == {"event": "ping", "org_id": org.org_id}
    (event,) = audit_actions(cloud_settings, org.org_id, "webhook.deliv")
    assert event.meta["event"] == "ping" and event.meta["status_code"] == 200 and event.meta["attempt"] == 1


def test_ping_of_a_failing_endpoint_reports_the_status_without_retrying(cloud_client, cloud_settings, org, sink):
    sink.statuses = [500]
    wid = create(cloud_client, org).json()["id"]
    r = cloud_client.post(f"{hooks_url(org.org_id)}/{wid}/ping", headers=key_headers(org.api_key))
    assert r.status_code == 200 and r.json() == {"delivered": False, "status": None, "status_class": "5xx"}
    assert len(sink.requests) == 1 and sink.sleeps == []
    (event,) = audit_actions(cloud_settings, org.org_id, "webhook.failed")
    assert event.meta["status_code"] == 500


def test_ping_network_error_has_no_status(cloud_client, cloud_settings, org, sink):
    sink.raises = httpx.ConnectError("boom with https://hooks.example.test/daas inside")
    wid = create(cloud_client, org).json()["id"]
    r = cloud_client.post(f"{hooks_url(org.org_id)}/{wid}/ping", headers=key_headers(org.api_key))
    assert r.json() == {"delivered": False, "status": None, "status_class": "error"}
    (event,) = audit_actions(cloud_settings, org.org_id, "webhook.failed")
    assert event.meta["error_type"] == "ConnectError"
    assert "hooks.example.test" not in json.dumps(event.meta)  # adres nie trafia do audytu


def test_ping_timeout_is_a_failure_not_an_error(cloud_client, cloud_settings, org, sink):
    sink.raises = httpx.ReadTimeout("slow")
    wid = create(cloud_client, org).json()["id"]
    r = cloud_client.post(f"{hooks_url(org.org_id)}/{wid}/ping", headers=key_headers(org.api_key))
    assert r.status_code == 200 and r.json()["delivered"] is False


def test_redirects_are_not_followed(cloud_client, cloud_settings, org, sink):
    sink.statuses = [302]
    sink.location = "http://127.0.0.1:8080/admin"
    wid = create(cloud_client, org).json()["id"]
    r = cloud_client.post(f"{hooks_url(org.org_id)}/{wid}/ping", headers=key_headers(org.api_key))
    assert r.json() == {"delivered": False, "status": None, "status_class": "3xx"}
    assert len(sink.requests) == 1  # drugiego zadania (pod Location) nie bylo


# ================================================================ SSRF przy dostawie
@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.1.2.3"], ["169.254.169.254"], ["::1"],
                                       [PUBLIC_IP, "192.168.0.7"], ["::ffff:10.0.0.1"], []])
def test_hostname_resolving_to_a_private_address_is_never_contacted(cloud_client, cloud_settings, org, sink,
                                                                     addresses):
    sink.addresses = addresses
    wid = create(cloud_client, org).json()["id"]
    r = cloud_client.post(f"{hooks_url(org.org_id)}/{wid}/ping", headers=key_headers(org.api_key))
    assert r.json() == {"delivered": False, "status": None, "status_class": "error"}
    assert sink.requests == []
    (event,) = audit_actions(cloud_settings, org.org_id, "webhook.failed")
    assert event.meta["error_type"] == "private_address"


def test_dns_failure_is_a_failure_without_contact(cloud_client, cloud_settings, org, sink):
    async def broken(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    cloud_client.app.state.webhooks.resolver = broken
    wid = create(cloud_client, org).json()["id"]
    r = cloud_client.post(f"{hooks_url(org.org_id)}/{wid}/ping", headers=key_headers(org.api_key))
    assert r.json()["delivered"] is False and sink.requests == []
    assert audit_actions(cloud_settings, org.org_id, "webhook.failed")[0].meta["error_type"] == "dns_failed"


def test_a_secret_that_no_longer_matches_is_not_used_for_signing(cloud_client, cloud_settings, org, sink):
    wid = create(cloud_client, org).json()["id"]
    update_rows(cloud_settings, models.Webhook, {"secret_hash": "0" * 64}, models.Webhook.id == wid)
    r = cloud_client.post(f"{hooks_url(org.org_id)}/{wid}/ping", headers=key_headers(org.api_key))
    assert r.json() == {"delivered": False, "status": None, "status_class": "error"} and sink.requests == []
    assert audit_actions(cloud_settings, org.org_id, "webhook.failed")[0].meta["error_type"] == "secret_unavailable"


# ================================================================ run.finished
def start_job(client: TestClient, org) -> dict:
    r = client.post(f"{API}/orgs/{org.org_id}/jobs", headers=key_headers(org.api_key),
                    json={"pipeline": "cs2_demo", "force_mock": True, "client_name": SECRET_CLIENT_NAME})
    assert r.status_code == 202, r.text
    job = r.json()

    def done():
        current = client.get(f"{API}/orgs/{org.org_id}/jobs/{job['id']}", headers=key_headers(org.api_key)).json()
        return current if current["status"] in ("succeeded", "failed", "cancelled") else None

    return wait_for(done)


def test_run_finished_is_delivered_with_a_verifiable_signature(cloud_client, cloud_settings, org, sink):
    created = create(cloud_client, org).json()
    job = start_job(cloud_client, org)
    assert job["status"] == "succeeded"
    wait_for(lambda: sink.requests)
    (req,) = sink.requests
    body = json.loads(req.content)
    assert set(body) == RUN_FINISHED_KEYS
    assert body["event"] == "run.finished" and body["org_id"] == org.org_id
    assert (body["job_id"], body["run_id"], body["pipeline"]) == (job["id"], job["run_id"], "cs2_demo")
    assert body["status"] == "succeeded" and body["run_status"] in {"SUCCESS", "PARTIAL"}
    assert body["finished_at"].startswith("20")
    assert req.headers["x-daas-event"] == "run.finished" and len(req.headers["x-daas-delivery"]) == 26
    assert req.headers["x-daas-signature"].startswith("sha256=")
    assert signature_ok(req, created["secret"])
    assert not signature_ok(req, "whsec_wrong")
    wait_for(lambda: audit_actions(cloud_settings, org.org_id, "webhook.deliv"))
    (event,) = audit_actions(cloud_settings, org.org_id, "webhook.deliv")
    assert event.meta["delivery_id"] == req.headers["x-daas-delivery"] and event.meta["event"] == "run.finished"


def test_payload_and_audit_contain_no_customer_data(cloud_client, cloud_settings, org, sink):
    create(cloud_client, org)
    start_job(cloud_client, org)
    wait_for(lambda: sink.requests)
    wait_for(lambda: audit_actions(cloud_settings, org.org_id, "webhook.deliv"))
    (req,) = sink.requests
    wire = req.content.decode() + json.dumps(dict(req.headers))
    assert SECRET_CLIENT_NAME not in wire and "Tajna" not in wire
    assert set(json.loads(req.content)) <= RUN_FINISHED_KEYS
    for event in audit_actions(cloud_settings, org.org_id, "webhook."):
        assert "Tajna" not in json.dumps(event.meta) and "hooks.example.test" not in json.dumps(event.meta)


def test_failed_delivery_is_attempted_three_times_with_backoff(cloud_client, cloud_settings, org, sink):
    sink.statuses = [503]
    create(cloud_client, org)
    start_job(cloud_client, org)
    wait_for(lambda: audit_actions(cloud_settings, org.org_id, "webhook.failed"))
    assert len(sink.requests) == 3
    assert sink.sleeps == [1.0, 5.0]                  # DAAS_WEBHOOK_BACKOFF_SEC=1,5,25: przerwy miedzy 3 probami
    assert len({r.headers["x-daas-delivery"] for r in sink.requests}) == 1   # to samo id przy ponowieniach
    assert len({r.content for r in sink.requests}) == 1
    (event,) = audit_actions(cloud_settings, org.org_id, "webhook.failed")
    assert event.meta["attempt"] == 3 and event.meta["status_code"] == 503
    assert not audit_actions(cloud_settings, org.org_id, "webhook.delivered")


def test_delivery_succeeds_on_a_retry(cloud_client, cloud_settings, org, sink):
    sink.statuses = [500, 200]
    created = create(cloud_client, org).json()
    start_job(cloud_client, org)
    wait_for(lambda: audit_actions(cloud_settings, org.org_id, "webhook.delivered"))
    assert len(sink.requests) == 2 and sink.sleeps == [1.0]
    assert all(signature_ok(r, created["secret"]) for r in sink.requests)
    (event,) = audit_actions(cloud_settings, org.org_id, "webhook.delivered")
    assert event.meta["attempt"] == 2 and event.meta["status_code"] == 200


def test_network_errors_are_retried_too(cloud_client, cloud_settings, org, sink):
    sink.raises = httpx.ConnectError("down")
    create(cloud_client, org)
    start_job(cloud_client, org)
    wait_for(lambda: audit_actions(cloud_settings, org.org_id, "webhook.failed"))
    assert len(sink.requests) == 3


def test_a_failing_webhook_does_not_fail_the_job_or_block_the_next_one(cloud_client, cloud_settings, org, sink):
    sink.statuses = [500]
    create(cloud_client, org)
    assert start_job(cloud_client, org)["status"] == "succeeded"
    assert start_job(cloud_client, org)["status"] == "succeeded"


def test_only_webhooks_of_the_same_organization_are_notified(cloud_client, cloud_settings, org, sink):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    create(cloud_client, other, url="https://other.example.test/x")
    create(cloud_client, org)
    start_job(cloud_client, org)
    wait_for(lambda: sink.requests)
    wait_for(lambda: audit_actions(cloud_settings, org.org_id, "webhook.deliv"))
    assert {r.headers["host"] for r in sink.requests} == {"hooks.example.test"}
    assert not audit_actions(cloud_settings, other.org_id)[1:]  # tylko wpis webhook.created


def test_every_webhook_of_the_organization_gets_the_event(cloud_client, cloud_settings, org, sink):
    create(cloud_client, org, url="https://a.example.test/x")
    create(cloud_client, org, url="https://b.example.test/x")
    start_job(cloud_client, org)
    wait_for(lambda: len(sink.requests) >= 2)
    assert {r.headers["host"] for r in sink.requests} == {"a.example.test", "b.example.test"}


def test_no_webhooks_means_no_deliveries(cloud_client, cloud_settings, org, sink):
    start_job(cloud_client, org)
    assert sink.requests == [] and not audit_actions(cloud_settings, org.org_id)


# ================================================================ pinning IP (M-4)
def ping(client: TestClient, org, wid: str):
    return client.post(f"{hooks_url(org.org_id)}/{wid}/ping", headers=key_headers(org.api_key))


def test_connection_goes_to_the_validated_ip_with_host_and_sni(cloud_client, cloud_settings, org, sink):
    wid = create(cloud_client, org, url="https://hooks.example.test:8443/daas?x=1").json()["id"]
    assert ping(cloud_client, org, wid).json()["delivered"] is True
    (req,) = sink.requests
    assert req.url.host == PUBLIC_IP and req.url.port == 8443 and req.url.path == "/daas"
    assert req.url.query == b"x=1"
    assert req.headers["host"] == "hooks.example.test:8443"
    assert req.extensions["sni_hostname"] == "hooks.example.test"        # certyfikat jest weryfikowany po nazwie


def test_default_port_host_header_has_no_port(cloud_client, cloud_settings, org, sink):
    wid = create(cloud_client, org).json()["id"]
    ping(cloud_client, org, wid)
    assert sink.requests[0].headers["host"] == "hooks.example.test"


def test_dns_is_resolved_once_per_attempt_so_rebinding_cannot_redirect_the_request(cloud_client, cloud_settings,
                                                                                    org, sink):
    answers = iter([[PUBLIC_IP], ["127.0.0.1"], ["10.0.0.1"], ["127.0.0.1"]])
    calls: list[str] = []

    async def rebinding(host: str, port: int) -> list[str]:
        calls.append(host)
        return next(answers)

    cloud_client.app.state.webhooks.resolver = rebinding
    wid = create(cloud_client, org).json()["id"]
    r = ping(cloud_client, org, wid)
    assert r.json()["delivered"] is True
    assert calls == ["hooks.example.test"]                  # jedno rozwiazanie na probe
    assert [req.url.host for req in sink.requests] == [PUBLIC_IP]   # zadne drugie rozwiazanie nie zmienia celu


def test_each_retry_resolves_again_and_pins_again(cloud_client, cloud_settings, org, sink):
    sink.statuses = [500, 200]
    addresses = iter([["93.184.216.34"], ["93.184.216.35"]])

    async def changing(host: str, port: int) -> list[str]:
        return next(addresses)

    cloud_client.app.state.webhooks.resolver = changing
    create(cloud_client, org)
    start_job(cloud_client, org)
    wait_for(lambda: len(sink.requests) >= 2)
    assert [r.url.host for r in sink.requests[:2]] == ["93.184.216.34", "93.184.216.35"]


def test_a_later_private_answer_is_refused_even_if_the_first_was_public(cloud_client, cloud_settings, org, sink):
    sink.statuses = [500, 200, 200]
    answers = iter([["93.184.216.34"], ["127.0.0.1"], ["127.0.0.1"]])

    async def rebinding(host: str, port: int) -> list[str]:
        return next(answers)

    cloud_client.app.state.webhooks.resolver = rebinding
    create(cloud_client, org)
    start_job(cloud_client, org)
    wait_for(lambda: audit_actions(cloud_settings, org.org_id, "webhook.failed"))
    assert [r.url.host for r in sink.requests] == ["93.184.216.34"]       # po przebindowaniu nic nie wyszlo


def test_ip_literal_urls_are_not_rewritten(cloud_client, cloud_settings, org, sink):
    wid = create(cloud_client, org, url=f"https://{PUBLIC_IP}/hook").json()["id"]
    ping(cloud_client, org, wid)
    (req,) = sink.requests
    assert req.url.host == PUBLIC_IP and "sni_hostname" not in req.extensions


def test_second_public_address_is_tried_when_the_first_cannot_connect(cloud_client, cloud_settings, org, sink):
    sink.addresses = ["93.184.216.34", "93.184.216.35"]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host.endswith(".34"):
            raise httpx.ConnectError("refused")
        return httpx.Response(200)

    cloud_client.app.state.webhooks.transport = httpx.MockTransport(handler)
    wid = create(cloud_client, org).json()["id"]
    assert ping(cloud_client, org, wid).json()["delivered"] is True
    assert calls == ["93.184.216.34", "93.184.216.35"]


@pytest.mark.parametrize(("status", "klass"), [(200, "2xx"), (204, "2xx"), (301, "3xx"), (404, "4xx"),
                                                (429, "4xx"), (500, "5xx"), (503, "5xx")])
def test_ping_reports_only_the_status_class(cloud_client, cloud_settings, org, sink, status, klass):
    sink.statuses = [status]
    wid = create(cloud_client, org).json()["id"]
    body = ping(cloud_client, org, wid).json()
    assert body == {"delivered": status < 300, "status": None, "status_class": klass}


def test_ping_without_a_response_is_class_error(cloud_client, cloud_settings, org, sink):
    sink.raises = httpx.ConnectError("down")
    wid = create(cloud_client, org).json()["id"]
    assert ping(cloud_client, org, wid).json()["status_class"] == "error"
    sink.raises = None
    sink.addresses = ["10.0.0.1"]
    assert ping(cloud_client, org, wid).json() == {"delivered": False, "status": None, "status_class": "error"}
