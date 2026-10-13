"""Powiadomienie o runie na czacie zespolu (Slack / Microsoft Teams / dowolny JSON): API harmonogramu (adres tylko do
zapisu, maskowany podpis, walidacja SSRF, semantyka pominiete/null), payloady trzech formatow, dostarczenie z
pinningiem adresu, audyt `schedule.webhook_notified`, niezaleznosc od maila po runie.

Siec zewnetrzna nie jest uzywana: transport to `httpx.MockTransport`, a rozwiazywanie nazw DNS jest podstawione.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient

from app.control import models
from app.control import schedule as sched
from app.control.chat_messages import CHAT_KINDS, build_payload, run_message
from app.control.executor import RunFinished
from app.control.tenancy import TenantPaths
from app.core.config import Settings
from app.sources.ecommerce_source import EcommerceSource
from tests.conftest import PROJECT_ROOT
from tests.v1_helpers import API, get_rows, key_headers, outbox, seed_member, seed_org, user_headers

CSV_BYTES = (PROJECT_ROOT / "data" / "fixtures" / "client_export_pl.csv").read_bytes()
SECRET_CLIENT_NAME = "Tajna Firma Sp. z o.o."
#: Wartosci z pliku klienta (fixture): nie moga trafic na czat.
CUSTOMER_VALUES = ("Fotel biurowy ERGO", "Biurko regulowane", "899,00", "2026/08/0001", "Meble")
PUBLIC_IP = "93.184.216.34"
#: Syntetyczne adresy; tajna czesc to `placeholderplaceholder` (ostatnie 4 znaki sciezki moga trafic do podpisu).
HIDDEN = "placeholderplaceholder"
SLACK_URL = f"https://hooks.slack.com/services/T0000/B0000/{HIDDEN}XyZ9"
TEAMS_URL = ("https://prod-00.westeurope.logic.azure.com/workflows/0000/triggers/manual/paths/invoke"
             f"?api-version=2016-06-01&sig={HIDDEN}")
GENERIC_URL = f"https://hooks.example.test/daas/{HIDDEN}"
RUN_FINISHED_KEYS = {"event", "org_id", "job_id", "run_id", "pipeline", "status", "run_status", "finished_at"}
GENERIC_KEYS = RUN_FINISHED_KEYS | {"organization", "period_from", "period_to", "report_sha256", "error_code", "link"}


# ================================================================ pomocnicze
@dataclass
class Sink:
    """Podstawiony odbiorca czatu: zapamietuje zadania i odpowiada kolejnymi statusami z `statuses`."""

    statuses: list[int] = field(default_factory=lambda: [200])
    raises: Exception | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    addresses: list[str] = field(default_factory=lambda: [PUBLIC_IP])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        status = self.statuses[min(len(self.requests), len(self.statuses)) - 1]
        return httpx.Response(status, content=b"ok")

    async def resolver(self, host: str, port: int) -> list[str]:
        return list(self.addresses)

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


@pytest.fixture()
def sink(cloud_client: TestClient) -> Sink:
    s = Sink()
    notifier = cloud_client.app.state.notifier
    notifier.transport = httpx.MockTransport(s.handler)
    notifier.resolver = s.resolver
    return s


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def schedule_url(org_id: str) -> str:
    return f"{API}/orgs/{org_id}/schedule"


def put(client: TestClient, org, **body):
    return client.put(schedule_url(org.org_id), json=body, headers=key_headers(org.api_key))


def stored_url(settings: Settings, org_id: str) -> str | None:
    (row,) = get_rows(settings, models.OrgSchedule, models.OrgSchedule.org_id == org_id)
    return row.notify_webhook_url


def wait_for(predicate, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def run_job(client: TestClient, org, body: dict) -> dict:
    r = client.post(f"{API}/orgs/{org.org_id}/jobs", json=body, headers=key_headers(org.api_key))
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]

    def done():
        job = client.get(f"{API}/orgs/{org.org_id}/jobs/{job_id}", headers=key_headers(org.api_key)).json()
        return job if job["status"] in ("succeeded", "failed", "cancelled") else None

    return wait_for(done)


def upload(client: TestClient, org) -> str:
    r = client.post(f"{API}/orgs/{org.org_id}/uploads", headers=key_headers(org.api_key),
                    files={"file": ("dane.csv", CSV_BYTES, "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def events(settings: Settings, org_id: str, action: str) -> list[models.AuditEvent]:
    return [e for e in get_rows(settings, models.AuditEvent, models.AuditEvent.org_id == org_id)
            if e.action == action]


def chat_events(settings: Settings, org_id: str) -> list[models.AuditEvent]:
    return events(settings, org_id, "schedule.webhook_notified")


def cs2_run(client: TestClient, org) -> dict:
    return run_job(client, org, {"pipeline": "cs2_demo", "force_mock": True})


# ================================================================ API: zapis adresu
def test_put_sets_the_address_and_responses_carry_only_a_masked_hint(cloud_client, cloud_settings, org):
    r = put(cloud_client, org, notify_webhook_url=SLACK_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["notify_webhook_set"] is True and body["notify_webhook_kind"] == "slack"
    assert body["notify_webhook_hint"] == "hooks.slack.com/\u2026XyZ9"
    assert "notify_webhook_url" not in body and HIDDEN not in r.text and "/services/" not in r.text
    got = cloud_client.get(schedule_url(org.org_id), headers=key_headers(org.api_key))
    assert got.status_code == 200 and got.json() == body and HIDDEN not in got.text
    assert stored_url(cloud_settings, org.org_id) == SLACK_URL


def test_audit_names_the_field_but_never_records_the_address(cloud_client, cloud_settings, org):
    assert put(cloud_client, org, notify_webhook_url=SLACK_URL).status_code == 200
    assert put(cloud_client, org, notify_webhook_kind="teams").status_code == 200
    first, second = events(cloud_settings, org.org_id, "schedule.updated")
    assert first.meta == {"fields": ["notify_webhook_url"]}
    assert second.meta == {"fields": ["notify_webhook_kind"]}  # adres pominiety = bez zmian, wiec nie ma go w polach
    every = json.dumps([e.meta for e in get_rows(cloud_settings, models.AuditEvent)])
    assert HIDDEN not in every and "hooks.slack.com" not in every
    audit_file = TenantPaths.for_org(cloud_settings, org.org_id).audit_log
    if audit_file.exists():
        assert HIDDEN not in audit_file.read_text(encoding="utf-8")


def test_omitted_address_stays_and_null_removes_it(cloud_client, cloud_settings, org):
    put(cloud_client, org, notify_webhook_url=GENERIC_URL, notify_webhook_kind="generic")
    r = put(cloud_client, org, reminder_enabled=True, notify_webhook_kind="generic")
    assert r.status_code == 200 and r.json()["notify_webhook_set"] is True
    assert stored_url(cloud_settings, org.org_id) == GENERIC_URL
    assert events(cloud_settings, org.org_id, "schedule.updated")[-1].meta == {"fields": ["reminder_enabled"]}

    r = put(cloud_client, org, notify_webhook_url=None)
    assert r.status_code == 200, r.text
    assert (r.json()["notify_webhook_set"], r.json()["notify_webhook_hint"]) == (False, None)
    assert stored_url(cloud_settings, org.org_id) is None
    assert "notify_webhook_url" in events(cloud_settings, org.org_id, "schedule.updated")[-1].meta["fields"]


def test_the_same_address_again_is_not_a_change(cloud_client, cloud_settings, org):
    put(cloud_client, org, notify_webhook_url=SLACK_URL)
    put(cloud_client, org, notify_webhook_url=f"  {SLACK_URL}  ")  # spacje z wklejania sa obcinane
    assert stored_url(cloud_settings, org.org_id) == SLACK_URL
    assert events(cloud_settings, org.org_id, "schedule.updated")[-1].meta == {"fields": []}


@pytest.mark.parametrize(("url", "reason"), [
    ("http://hooks.example.test/daas/x", "https_required"),
    ("https://127.0.0.1/x", "private_address"),
    ("https://10.0.0.1/x", "private_address"),
    ("https://[::1]/x", "private_address"),
    ("https://localhost/x", "private_address"),
    ("https://printer.local/x", "private_address"),
    ("https://user:pass@hooks.example.test/x", "credentials_in_url"),
    ("ftp://hooks.example.test/x", "url_malformed"),
    ("https://hooks.example.test/a b", "url_malformed"),
    ("not a url", "url_malformed"),
    ("", "url_malformed"),
])
def test_unsafe_or_malformed_address_is_422_naming_the_field_without_echo(cloud_client, cloud_settings, org,
                                                                         url, reason):
    r = put(cloud_client, org, notify_webhook_url=url)
    assert r.status_code == 422, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["errors"] == [{"loc": ["body", "notify_webhook_url"], "msg": body["errors"][0]["msg"],
                               "type": reason}]
    for echoed in ("hooks.example.test", "127.0.0.1", "10.0.0.1", "user:pass", "printer.local", "not a url"):
        assert echoed not in r.text, echoed
    assert get_rows(cloud_settings, models.OrgSchedule) == []  # nic nie zapisano


def test_address_longer_than_512_characters_is_422(cloud_client, cloud_settings, org):
    prefix = "https://hooks.example.test/"
    exact = prefix + "a" * (512 - len(prefix))
    assert put(cloud_client, org, notify_webhook_url=exact).status_code == 200
    r = put(cloud_client, org, notify_webhook_url=exact + "a")
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    assert any(e["loc"] == ["body", "notify_webhook_url"] for e in r.json()["errors"])
    assert "a" * 100 not in r.text
    assert stored_url(cloud_settings, org.org_id) == exact  # poprzedni adres zostaje


def test_rejected_address_keeps_the_saved_one(cloud_client, cloud_settings, org):
    put(cloud_client, org, notify_webhook_url=SLACK_URL)
    assert put(cloud_client, org, notify_webhook_url="https://10.0.0.1/x").status_code == 422
    assert stored_url(cloud_settings, org.org_id) == SLACK_URL


@pytest.mark.parametrize("kind", ["discord", "SLACK", "", None])
def test_unknown_kind_is_422(cloud_client, cloud_settings, org, kind):
    r = put(cloud_client, org, notify_webhook_kind=kind)
    assert r.status_code == 422, r.text
    assert any(e["loc"][-1] == "notify_webhook_kind" for e in r.json()["errors"])


def test_dev_mode_accepts_http_and_loopback(cloud_factory):
    client, settings = cloud_factory(APP_ENV="dev")
    seeded = seed_org(settings)
    r = put(client, seeded, notify_webhook_url="http://127.0.0.1:9999/hook", notify_webhook_kind="generic")
    assert r.status_code == 200, r.text
    assert r.json()["notify_webhook_hint"] == "127.0.0.1"  # krotka sciezka nie jest ujawniana


def test_member_sees_the_hint_but_cannot_change_the_address(cloud_client, cloud_settings, org):
    put(cloud_client, org, notify_webhook_url=SLACK_URL)
    member = user_headers(cloud_settings, seed_member(cloud_settings, org.org_id, "m@example.test", "member"))
    got = cloud_client.get(schedule_url(org.org_id), headers=member)
    assert got.status_code == 200 and got.json()["notify_webhook_hint"] == "hooks.slack.com/\u2026XyZ9"
    assert HIDDEN not in got.text
    r = cloud_client.put(schedule_url(org.org_id), json={"notify_webhook_url": None}, headers=member)
    assert r.status_code == 403
    assert stored_url(cloud_settings, org.org_id) == SLACK_URL


@pytest.mark.parametrize(("url", "hint"), [
    (SLACK_URL, "hooks.slack.com/\u2026XyZ9"),
    (TEAMS_URL, "prod-00.westeurope.logic.azure.com/\u2026voke"),        # zapytanie (z `sig`) nigdy
    ("https://HOOKS.Example.TEST:8443/daas/notify-0001/", "hooks.example.test/\u20260001"),
    ("https://hooks.example.test/short", "hooks.example.test"),         # krotka sciezka: sam host
    ("https://hooks.example.test", "hooks.example.test"),
    (None, None), ("", None),
])
def test_hint_shows_the_host_and_at_most_four_characters_of_a_long_path(url, hint):
    assert sched.webhook_hint(url) == hint


# ================================================================ dostarczenie: formaty
def test_slack_message_has_status_period_hash_and_link_but_no_report_content(cloud_client, cloud_settings, org,
                                                                              sink):
    put(cloud_client, org, notify_webhook_url=SLACK_URL)
    job = run_job(cloud_client, org, {"pipeline": "ecommerce_demo", "upload_id": upload(cloud_client, org),
                                      "client_name": SECRET_CLIENT_NAME})
    assert job["status"] == "succeeded", job
    (event,) = wait_for(lambda: chat_events(cloud_settings, org.org_id))
    (record,) = get_rows(cloud_settings, models.RunRecord, models.RunRecord.run_id == job["run_id"])
    (req,) = sink.requests
    assert req.method == "POST" and req.headers["content-type"] == "application/json"
    assert req.url.host == PUBLIC_IP and req.headers["host"] == "hooks.slack.com"   # pinning jak webhooki HMAC
    assert req.extensions["sni_hostname"] == "hooks.slack.com"
    assert req.url.path == f"/services/T0000/B0000/{HIDDEN}XyZ9"
    assert "x-daas-signature" not in req.headers
    body = sink.body()
    assert set(body) == {"text", "blocks"} and "\n" not in body["text"]
    assert body["text"].startswith("Raport jest gotowy: ecommerce_demo")
    section, actions = body["blocks"]
    assert section["type"] == "section" and section["text"]["type"] == "mrkdwn"
    text = section["text"]["text"]
    assert "*Raport jest gotowy*" in text and "*Status:* zakończony" in text and "ecommerce_demo" in text
    assert record.sha256_html and record.sha256_html in text
    assert f"{record.period_from.isoformat()} – {record.period_to.isoformat()}" in text
    assert "Kod błędu" not in text
    (button,) = actions["elements"]
    assert actions["type"] == "actions" and button["type"] == "button"
    assert button["url"] == f"https://cockpit.example.test/runs/{job['run_id']}"
    wire = req.content.decode()
    for forbidden in (SECRET_CLIENT_NAME, "Tajna", "token", "<html", *CUSTOMER_VALUES):
        assert forbidden not in wire, forbidden
    assert event.meta == {"run_id": job["run_id"], "job_id": job["id"], "status": record.status, "channel": "slack",
                          "sent": True, "status_code": 200}
    assert event.actor_type == "system" and outbox(cloud_settings) == []  # mail jest osobna opcja (wylaczona)


def test_teams_message_is_an_adaptive_card(cloud_client, cloud_settings, org, sink):
    put(cloud_client, org, notify_webhook_url=TEAMS_URL, notify_webhook_kind="teams")
    job = cs2_run(cloud_client, org)
    (event,) = wait_for(lambda: chat_events(cloud_settings, org.org_id))
    (req,) = sink.requests
    assert req.url.params["sig"] == HIDDEN and req.headers["host"] == "prod-00.westeurope.logic.azure.com"
    body = sink.body()
    assert body["type"] == "message"
    (attachment,) = body["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.4"
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    title, facts = card["body"]
    assert title["type"] == "TextBlock" and title["text"] == "Raport jest gotowy" and title["color"] == "Good"
    assert facts["type"] == "FactSet"
    values = {f["title"]: f["value"] for f in facts["facts"]}
    assert values["Pipeline"] == "cs2_demo" and values["Status"] == "zakończony" and values["Organizacja"] == "Acme"
    assert "Okres danych" not in values  # cs2_demo nie ma okresu danych
    assert card["actions"] == [{"type": "Action.OpenUrl", "title": "Otwórz run",
                                "url": f"https://cockpit.example.test/runs/{job['run_id']}"}]
    assert event.meta["channel"] == "teams" and event.meta["sent"] is True


def test_generic_message_is_plain_json_with_english_fields_and_no_signature(cloud_client, cloud_settings, org,
                                                                            sink):
    put(cloud_client, org, notify_webhook_url=GENERIC_URL, notify_webhook_kind="generic")
    job = cs2_run(cloud_client, org)
    wait_for(lambda: chat_events(cloud_settings, org.org_id))
    (req,) = sink.requests
    assert not any(h.startswith("x-daas-") for h in req.headers)  # adres jest sekretem; podpis daje /webhooks
    body = sink.body()
    assert set(body) == GENERIC_KEYS
    assert body["event"] == "run.finished" and body["org_id"] == org.org_id and body["organization"] == "Acme"
    assert (body["job_id"], body["run_id"], body["pipeline"]) == (job["id"], job["run_id"], "cs2_demo")
    assert body["status"] == "succeeded" and body["error_code"] is None and body["period_from"] is None
    assert body["link"] == f"https://cockpit.example.test/runs/{job['run_id']}"
    assert body["finished_at"].startswith("20")


def test_failed_run_message_carries_the_error_code(cloud_client, cloud_settings, org, sink, monkeypatch):
    def broken(self, force_mock=False, source_path=None):
        raise ValueError("Fotel biurowy ERGO: parse error")  # tresc wyjatku (z danymi) nie moze trafic na czat

    monkeypatch.setattr(EcommerceSource, "fetch", broken)
    put(cloud_client, org, notify_webhook_url=SLACK_URL)
    job = run_job(cloud_client, org, {"pipeline": "ecommerce_demo", "upload_id": upload(cloud_client, org)})
    assert job["status"] == "failed" and job["error_code"], job
    (event,) = wait_for(lambda: chat_events(cloud_settings, org.org_id))
    text = sink.body()["blocks"][0]["text"]["text"]
    assert "*Przetwarzanie raportu nie powiodło się*" in text and "*Status:* błąd" in text
    assert f"*Kod błędu:* {job['error_code']}" in text
    assert "Fotel" not in sink.requests[0].content.decode() and "parse error" not in sink.requests[0].content.decode()
    assert event.meta["status"] == "FAILED" and event.meta["sent"] is True


def _message(settings: Settings, run_status: str = "PARTIAL", status: str = "succeeded", org_name: str = "Firma"):
    event = RunFinished(org_id="01ORG", job_id="01JOB", run_id="run-1", pipeline="uksc_evidence", status=status,
                        run_status=run_status, finished_at_iso="2026-10-12T08:00:00+00:00")
    return run_message(settings, event, org_name=org_name, period=("2026-10-05", "2026-10-11"),
                       report_sha256="a" * 64, error_code=None)


def test_partial_run_is_called_partial_in_every_format():
    settings = Settings(_env_file=None, daas_cockpit_origin="https://cockpit.example.test/")
    message = _message(settings)
    slack = build_payload("slack", message)
    assert "Raport jest gotowy (częściowy)" in slack["text"]
    assert "*Status:* zakończony częściowo" in slack["blocks"][0]["text"]["text"]
    assert "*Zakończono:* 2026-10-12 08:00 UTC" in slack["blocks"][0]["text"]["text"]
    title = build_payload("teams", message)["attachments"][0]["content"]["body"][0]
    assert title["text"] == "Raport jest gotowy (częściowy)" and title["color"] == "Warning"
    generic = build_payload("generic", message)
    assert generic["run_status"] == "PARTIAL" and generic["status"] == "succeeded"
    assert generic["link"] == "https://cockpit.example.test/runs/run-1"
    assert (generic["period_from"], generic["period_to"]) == ("2026-10-05", "2026-10-11")
    failed = build_payload("teams", _message(settings, run_status="PARTIAL", status="failed"))
    assert failed["attachments"][0]["content"]["body"][0]["color"] == "Attention"  # porazka ma pierwszenstwo


def test_org_name_is_sanitized_and_escaped_in_every_format():
    settings = Settings(_env_file=None, daas_cockpit_origin="https://cockpit.example.test")
    message = _message(settings, org_name="Sklep <!channel> & Co https://evil.example.test www.evil.example.test")
    for kind in CHAT_KINDS:
        wire = json.dumps(build_payload(kind, message), ensure_ascii=False)
        assert "evil" not in wire and "<!channel>" not in wire, kind
    slack = build_payload("slack", message)["blocks"][0]["text"]["text"]
    assert "*Organizacja:* Sklep !channel &amp; Co" in slack  # `<` `>` usuniete, `&` jako encja mrkdwn


def test_unknown_kind_cannot_be_built():
    with pytest.raises(ValueError):
        build_payload("discord", _message(Settings(_env_file=None)))


# ================================================================ dostarczenie: SSRF, bledy, niezaleznosc od maila
def test_a_name_resolving_to_a_private_address_is_never_contacted(cloud_client, cloud_settings, org, sink):
    sink.addresses = ["10.0.0.1"]
    put(cloud_client, org, notify_webhook_url=SLACK_URL)
    job = cs2_run(cloud_client, org)
    assert job["status"] == "succeeded"
    (event,) = wait_for(lambda: chat_events(cloud_settings, org.org_id))
    assert sink.requests == []
    assert event.meta["sent"] is False and event.meta["error_type"] == "private_address"
    assert "status_code" not in event.meta


@pytest.mark.parametrize(("statuses", "raises", "status_code", "error_type"), [
    ([500], None, 500, "http_status"),
    ([404], None, 404, "http_status"),
    ([302], None, 302, "http_status"),              # przekierowanie nie jest sledzone
    ([200], httpx.ReadTimeout("slow"), None, "ReadTimeout"),
    ([200], httpx.ConnectError("down"), None, "ConnectError"),
])
def test_receiver_failure_is_audited_once_and_the_job_still_succeeds(cloud_client, cloud_settings, org, sink,
                                                                     statuses, raises, status_code, error_type):
    sink.statuses, sink.raises = statuses, raises
    put(cloud_client, org, notify_webhook_url=SLACK_URL)
    job = cs2_run(cloud_client, org)
    assert job["status"] == "succeeded"
    (event,) = wait_for(lambda: chat_events(cloud_settings, org.org_id))
    assert len(sink.requests) == 1  # jedna proba, bez ponowien
    assert event.meta["sent"] is False and event.meta["error_type"] == error_type
    assert event.meta.get("status_code") == status_code


def test_mail_and_chat_together_send_one_mail_and_one_post(cloud_client, cloud_settings, org, sink):
    put(cloud_client, org, notify_on_finish=True, notify_webhook_url=GENERIC_URL, notify_webhook_kind="generic")
    job = cs2_run(cloud_client, org)
    wait_for(lambda: chat_events(cloud_settings, org.org_id) and events(cloud_settings, org.org_id,
                                                                         "schedule.notified"))
    time.sleep(0.2)
    (mail,) = outbox(cloud_settings, org.email)
    assert mail["link"].endswith(f"/runs/{job['run_id']}")
    assert len(sink.requests) == 1 and sink.body()["run_id"] == job["run_id"]


def test_chat_failure_does_not_block_the_mail(cloud_client, cloud_settings, org, sink):
    sink.raises = httpx.ConnectError("down")
    put(cloud_client, org, notify_on_finish=True, notify_webhook_url=SLACK_URL)
    cs2_run(cloud_client, org)
    (chat,) = wait_for(lambda: chat_events(cloud_settings, org.org_id))
    (mail_event,) = wait_for(lambda: events(cloud_settings, org.org_id, "schedule.notified"))
    assert chat.meta["sent"] is False and mail_event.meta["sent"] is True
    assert len(outbox(cloud_settings, org.email)) == 1


def test_mail_failure_does_not_block_the_chat(cloud_client, cloud_settings, org, sink, monkeypatch):
    def boom(mail):
        raise OSError("smtp down")

    monkeypatch.setattr(cloud_client.app.state.mailer, "send", boom)
    put(cloud_client, org, notify_on_finish=True, notify_webhook_url=SLACK_URL)
    cs2_run(cloud_client, org)
    (chat,) = wait_for(lambda: chat_events(cloud_settings, org.org_id))
    assert chat.meta["sent"] is True and len(sink.requests) == 1


def test_one_run_one_post(cloud_client, cloud_settings, org, sink):
    put(cloud_client, org, notify_webhook_url=SLACK_URL)
    for _ in range(2):
        cs2_run(cloud_client, org)
    wait_for(lambda: len(chat_events(cloud_settings, org.org_id)) == 2)
    time.sleep(0.2)
    assert len(sink.requests) == 2
    assert len({e.meta["run_id"] for e in chat_events(cloud_settings, org.org_id)}) == 2


def test_no_post_without_an_address(cloud_client, cloud_settings, org, sink):
    put(cloud_client, org, notify_webhook_url=SLACK_URL)
    put(cloud_client, org, notify_webhook_url=None)
    cs2_run(cloud_client, org)
    time.sleep(0.3)
    assert sink.requests == [] and chat_events(cloud_settings, org.org_id) == []


def test_only_the_organization_of_the_run_is_notified(cloud_client, cloud_settings, org, sink):
    other = seed_org(cloud_settings, name="Other", owner_email="other@example.test")
    put(cloud_client, other, notify_webhook_url=GENERIC_URL, notify_webhook_kind="generic")
    put(cloud_client, org, notify_webhook_url=SLACK_URL)
    cs2_run(cloud_client, org)
    wait_for(lambda: chat_events(cloud_settings, org.org_id))
    time.sleep(0.2)
    assert [r.headers["host"] for r in sink.requests] == ["hooks.slack.com"]
    assert chat_events(cloud_settings, other.org_id) == []


def test_the_address_never_reaches_the_logs(cloud_client, cloud_settings, org, sink, caplog):
    caplog.set_level(logging.INFO)
    put(cloud_client, org, notify_webhook_url=TEAMS_URL, notify_webhook_kind="teams")
    cs2_run(cloud_client, org)
    wait_for(lambda: chat_events(cloud_settings, org.org_id))
    assert len(sink.requests) == 1
    assert HIDDEN not in caplog.text and "/paths/invoke" not in caplog.text
    assert any("chat notification sent" in r.getMessage() for r in caplog.records)
    # linia klienta httpx jest, ale bez sciezki i zapytania (Teams trzyma podpis `sig` w zapytaniu)
    assert any(r.name == "httpx" and f"POST https://{PUBLIC_IP}/..." in r.getMessage() for r in caplog.records)


def test_partial_run_status_reaches_the_chat_audit(cloud_client, cloud_settings, org):
    """`RunNotifier` wolany wprost (transport i resolver podstawione jak w `WebhookDispatcher`): run `PARTIAL`."""
    from app.control.db import create_engine, make_sessionmaker
    from app.control.mailer import build_mailer
    from app.control.notifications import RunNotifier

    put(cloud_client, org, notify_webhook_url=GENERIC_URL, notify_webhook_kind="generic")
    s = Sink()
    event = RunFinished(org_id=org.org_id, job_id="01JOB", run_id="run-partial", pipeline="uksc_evidence",
                        status="succeeded", run_status="PARTIAL", finished_at_iso="2026-10-12T08:00:00+00:00")

    async def go():
        engine = create_engine(cloud_settings)
        try:
            notifier = RunNotifier(cloud_settings, make_sessionmaker(engine), build_mailer(cloud_settings),
                                   transport=httpx.MockTransport(s.handler), resolver=s.resolver)
            await notifier.on_run_finished(event)
            await notifier.drain()
        finally:
            await engine.dispose()

    asyncio.run(go())
    assert s.body()["run_status"] == "PARTIAL" and s.body()["link"].endswith("/runs/run-partial")
    (audit,) = chat_events(cloud_settings, org.org_id)
    assert audit.meta == {"run_id": "run-partial", "job_id": "01JOB", "status": "PARTIAL", "channel": "generic",
                          "sent": True, "status_code": 200}
