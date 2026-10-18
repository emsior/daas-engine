"""N1: korekta/zwrot (ujemny przychod i koszt) przechodzi caly pipeline: upload -> job -> run -> raporty.

`test_csv_negative_revenue.py` sprawdza tylko mapper; tu chodzi o model rekordu i dalsze etapy (zapis, transformy,
raporty JSON/MD/HTML).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.control.tenancy import TenantPaths
from app.core.config import Settings
from tests.test_runs import run_pipeline
from tests.v1_helpers import API, key_headers, seed_org

HEADER = "order_id,date,category,product,quantity,revenue,cost,discount\n"
NEG_WARNING = "ujemnym przychodem"


@pytest.fixture()
def org(cloud_client: TestClient, cloud_settings: Settings):
    return seed_org(cloud_settings)


def csv_bytes(*rows: str) -> bytes:
    return (HEADER + "\n".join(rows) + "\n").encode()


def report_files(settings: Settings, org, run_id: str) -> dict:
    reports = TenantPaths.for_org(settings, org.org_id).reports
    return {p.suffix[1:]: p for p in reports.iterdir() if run_id[:8] in p.name and "latest" not in p.name}


def v1_run(client, settings, org, data: bytes) -> tuple[dict, dict]:
    job = run_pipeline(client, org, "ecommerce_demo", filename="dane.csv", data=data)
    files = report_files(settings, org, job["run_id"])
    assert set(files) >= {"json", "md", "html"}
    return job, files


# 100 + (-100) = 0: przychod calkowity 0 -> marza nieokreslona
ZERO = csv_bytes("1,2026-09-01,A,P1,1,100,50,0", "2,2026-09-02,A,P1,1,-100,-50,0")
# 300 + (-100) = 200
NET = csv_bytes("1,2026-09-01,A,P1,1,300,150,0", "2,2026-09-02,A,P1,1,-100,-50,0")


def test_correction_row_does_not_fail_the_run(cloud_client, cloud_settings, org):
    job, files = v1_run(cloud_client, cloud_settings, org, NET)
    assert job["status"] == "succeeded" and job["error_code"] is None
    payload = json.loads(files["json"].read_text(encoding="utf-8"))
    m = payload["metrics"]
    assert m["orders_total"] == 2
    assert m["revenue_total"] == pytest.approx(200.0)
    assert m["cost_total"] == pytest.approx(100.0)
    assert m["profit_total"] == pytest.approx(100.0)
    assert m["margin_pct"] == pytest.approx(50.0)
    assert NEG_WARNING in payload["source_detail"]
    assert payload["records_processed"] == 2


def test_correction_row_is_not_a_loss_order_nor_an_anomaly(cloud_client, cloud_settings, org):
    _job, files = v1_run(cloud_client, cloud_settings, org, NET)
    m = json.loads(files["json"].read_text(encoding="utf-8"))["metrics"]
    assert all(o["order_id"] != "2" for o in m["loss_orders"]) and m["loss_orders_count"] == 0
    assert all(a["order_id"] != "2" for a in m["anomalies"])


def test_report_endpoints_serve_the_run(cloud_client, cloud_settings, org):
    job, _files = v1_run(cloud_client, cloud_settings, org, NET)
    for fmt in ("html", "md", "json"):
        r = cloud_client.get(f"{API}/orgs/{org.org_id}/runs/{job['run_id']}/report", params={"fmt": fmt},
                             headers=key_headers(org.api_key))
        assert r.status_code == 200, (fmt, r.text)


def test_legacy_upload_and_run_with_a_correction_row(client: TestClient):
    up = client.post("/upload", files={"file": ("k.csv", NET, "text/csv")})
    assert up.status_code == 200, up.text
    assert any(NEG_WARNING in w for w in up.json()["warnings"])
    r = client.post("/run", json=up.json()["run_payload"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_status"] in {"SUCCESS", "PARTIAL"} and body["records_processed"] == 2
    assert body["metrics"]["revenue_total"] == pytest.approx(200.0)
    assert client.get(f"/reports/run/{body['run_id']}").status_code == 200
