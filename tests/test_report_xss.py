"""Raporty: wartosci z danych klienta nie moga trafic do HTML jako znaczniki.

Trzy renderery Markdown (`generator.py` dla ecommerce i esports, `uksc_report.py`) i dwie trasy
(`/reports/{pipeline}/latest`, `/reports/run/{run_id}`). Ladunek idzie przez kazde pole, ktore
renderer wypisuje. Dodatkowo: nagłówki odpowiedzi raportu i renderer HTML bez surowego HTML.
"""
from __future__ import annotations

import copy
import io
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.models import DataStatus, RunRequest, RunStatus
from app.pipelines.runner import PipelineRunner
from app.reporting.html import render_html
from app.sources.cs2_source import CS2Source, SourceResult
from app.sources.uksc_source import compute_evidence_hash, compute_package_hash
from tests.conftest import PROJECT_ROOT

PAYLOADS = [
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(2)">',
    '"><svg onload=alert(3)>',
    "[klik](javascript:alert(4))",
    "<iframe src=//example.invalid></iframe>",
]

#: Znaczniki, ktore moga wystapic w raporcie. Wszystko inne (script, img, svg, iframe, a, ...) = blad.
ALLOWED_TAGS = {
    "html", "head", "meta", "title", "style", "body", "div", "span", "br", "strong", "em", "h1",
    "h2", "h3", "table", "thead", "tbody", "tr", "th", "td", "p", "ul", "li", "code", "blockquote",
    "hr", "pre",
}

REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"


class _TagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.bad_attrs: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            if name.startswith("on") or (value or "").lower().startswith("javascript:"):
                self.bad_attrs.append(f"{tag}[{name}={value}]")


def assert_no_raw_markup(html_text: str) -> None:
    parser = _TagCollector()
    parser.feed(html_text)
    unexpected = sorted(set(parser.tags) - ALLOWED_TAGS)
    assert not unexpected, f"niedozwolone znaczniki w raporcie: {unexpected}"
    assert not parser.bad_attrs, f"niebezpieczne atrybuty: {parser.bad_attrs}"
    assert parser.tags.count("style") == 1
    assert "<script" not in html_text.lower()


def _payload_csv() -> bytes:
    """CSV z ladunkiem w order_id, product, category, w naglowku nieznanej kolumny i stratnym zamowieniu."""
    rows = ["order_id,date,product,category,quantity,revenue,cost,discount,status,"
            + PAYLOADS[1].replace(",", ";").replace('"', "'")]
    for i, p in enumerate(PAYLOADS):
        safe = p.replace(",", ";").replace('"', "'")
        # revenue < cost -> zamowienie stratne (trafia do tabeli strat, anomalii i wnioskow)
        rows.append(f"{safe}{i},2026-08-0{i + 1},{safe},{safe},1,50.00,90.00,0.25,completed,x")
    rows.append("ORD-OK,2026-08-09,Zwykly produkt,Zwykla kategoria,1,200.00,100.00,0,completed,x")
    return ("\n".join(rows) + "\n").encode("utf-8")


# ---------------------------------------------------------------- ecommerce: upload -> run -> raporty
@pytest.mark.parametrize("fmt", ["html", "md"])
def test_ecommerce_payload_in_every_field_is_inert(client: TestClient, fmt: str):
    up = client.post("/upload", files={"file": ("dane.csv", io.BytesIO(_payload_csv()), "text/csv")},
                     data={"client_name": PAYLOADS[0] + PAYLOADS[1]})
    assert up.status_code == 200, up.text
    run = client.post("/run", json=up.json()["run_payload"])
    assert run.status_code == 200, run.text
    run_id = run.json()["run_id"]

    for url in ("/reports/ecommerce_demo/latest", f"/reports/run/{run_id}"):
        r = client.get(url, params={"fmt": fmt})
        assert r.status_code == 200, (url, r.text)
        if fmt == "html":
            assert_no_raw_markup(r.text)
            assert "&lt;script&gt;" in r.text  # dane nie znikaja — sa pokazane jako tekst
        else:
            assert "<script" not in r.text.lower() and "<img" not in r.text.lower()
            assert "<svg" not in r.text.lower() and "<iframe" not in r.text.lower()


# ---------------------------------------------------------------- esports (cs2_demo)
def test_cs2_payload_in_every_field_is_inert(client: TestClient, settings: Settings,
                                             monkeypatch: pytest.MonkeyPatch):
    src = CS2Source(settings)._load_fixture("fixture")
    records = copy.deepcopy(src.records)
    for i, rec in enumerate(records):
        p = PAYLOADS[i % len(PAYLOADS)]
        rec["map"], rec["opponent"], rec["team"] = p, p, p

    monkeypatch.setattr(CS2Source, "fetch", lambda self, force_mock=False, limit=20: SourceResult(
        records=records, data_status=DataStatus.MOCK_DATA, detail=PAYLOADS[0]))

    run = client.post("/run", json={"pipeline": "cs2_demo", "client_name": PAYLOADS[2]})
    assert run.status_code == 200, run.text
    for url in ("/reports/cs2_demo/latest", f"/reports/run/{run.json()['run_id']}"):
        r = client.get(url)
        assert r.status_code == 200
        assert_no_raw_markup(r.text)
        assert "&lt;" in r.text


# ---------------------------------------------------------------- uksc_evidence
def _poisoned_package(payload: str) -> dict:
    raw = json.loads((PROJECT_ROOT / "data" / "fixtures" / "uksc" / "host_noncompliant.json")
                     .read_text(encoding="utf-8"))

    def poison(node):
        if isinstance(node, dict):
            return {k: poison(v) for k, v in node.items()}
        if isinstance(node, list):
            return [poison(v) for v in node]
        return payload if isinstance(node, str) else node

    raw["host"]["name"] = payload
    for check in raw["checks"]:
        check["title"] = payload
        check["uksc_ref"] = payload
        check["reference_threshold"] = payload
        check["evidence"] = poison(check["evidence"])
        check["evidence"]["attestation_hint"] = payload
        check["evidence_hash"] = compute_evidence_hash(check["evidence"])
    raw["package_sha256"] = compute_package_hash([c["evidence_hash"] for c in raw["checks"]])
    return raw


@pytest.mark.parametrize("payload", PAYLOADS)
def test_uksc_payload_in_every_field_is_inert(client: TestClient, runner: PipelineRunner,
                                              settings: Settings, payload: str):
    uploads = Path(settings.reports_path.parent / "uploads")
    uploads.mkdir(parents=True, exist_ok=True)
    pkg = uploads / "pkg.json"
    pkg.write_text(json.dumps(_poisoned_package(payload)), encoding="utf-8")

    res = runner.run(RunRequest(pipeline="uksc_evidence", source_path=str(pkg), client_name=payload))
    assert res.run_status == RunStatus.SUCCESS, res.error
    assert res.data_status == DataStatus.LIVE_DATA

    for url in ("/reports/uksc_evidence/latest", f"/reports/run/{res.run_id}"):
        r = client.get(url)
        assert r.status_code == 200
        assert_no_raw_markup(r.text)
        assert "&lt;" in r.text or "klik" in r.text


# ---------------------------------------------------------------- renderer HTML nie przepuszcza surowego HTML
def test_render_html_drops_raw_html_from_markdown():
    md = ("<script>alert(1)</script>\n\n<b onclick=\"x()\">pogrubione</b> tekst\n\n"
          "| a | b |\n|---|---|\n| <img src=x onerror=alert(1)> | [x](javascript:alert(1)) |\n")
    out = render_html(md, title="t", data_status="MOCK_DATA", narrative=None, narrative_source=None)
    parser = _TagCollector()
    parser.feed(out)
    assert not (set(parser.tags) - ALLOWED_TAGS)
    assert not parser.bad_attrs
    assert "<script" not in out.lower()


def test_render_html_escapes_title_client_and_narrative():
    out = render_html("# x\n", title="<b>t</b>", data_status="MOCK_DATA",
                      narrative="<script>1</script>", narrative_source="TEMPLATE",
                      client_name="<i>k</i>")
    assert "<script" not in out and "<b>t</b>" not in out and "<i>k</i>" not in out


# ---------------------------------------------------------------- naglowki odpowiedzi raportu
@pytest.mark.parametrize("fmt", ["html", "md", "json"])
def test_report_responses_carry_security_headers(client: TestClient, fmt: str):
    run = client.post("/run", json={"pipeline": "ecommerce_demo"})
    assert run.status_code == 200
    for url in ("/reports/ecommerce_demo/latest", f"/reports/run/{run.json()['run_id']}"):
        r = client.get(url, params={"fmt": fmt})
        assert r.status_code == 200
        assert r.headers["content-security-policy"] == REPORT_CSP
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        # raport ma sie osadzac w iframe tej samej domeny: bez X-Frame-Options
        assert "x-frame-options" not in r.headers
