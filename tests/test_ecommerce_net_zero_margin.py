"""N3: marza calkowita przy przychodzie <= 0 jest nieokreslona (None w JSON, `n/d` w raportach), nie `0,00 %`."""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.reporting.fmt import pln
from app.reporting.narrative import template_narrative
from app.transforms.ecommerce_transform import transform_ecommerce
from tests.test_ecommerce_negative_pipeline import (  # noqa: F401
    NEG_WARNING,
    NET,
    ZERO,
    csv_bytes,
    org,
    v1_run,
)


def order(oid: str, revenue: float, cost: float, status: str = "completed") -> dict:
    return {"order_id": oid, "date": "2026-01-05", "product": "P", "category": "A", "quantity": 1, "revenue": revenue,
            "cost": cost, "profit": revenue - cost, "discount": 0.0, "status": status}


@pytest.mark.parametrize("rows", [
    [order("1", 100, 50), order("2", -100, -50)],      # suma 0
    [order("1", -100, -50), order("2", -20, -10)],     # suma ujemna
    [order("1", 0, 10)],                                 # darmowe zamowienie
])
def test_total_margin_and_aov_are_none_when_total_revenue_is_not_positive(rows):
    m = transform_ecommerce(rows)
    assert m.revenue_total <= 0
    assert m.margin_pct is None and m.avg_order_value is None


def test_positive_total_keeps_numeric_margin_and_aov():
    m = transform_ecommerce([order("1", 300, 150), order("2", -100, -50)])
    assert m.revenue_total == 200 and m.margin_pct == 50.0 and m.avg_order_value == 100.0


def test_markdown_and_narrative_say_nd_for_the_undefined_margin():
    m = transform_ecommerce([order("1", 100, 50), order("2", -100, -50)])
    text = template_narrative("ecommerce_demo", m)
    assert "marżę n/d" in text and "marżę 0,00 %" not in text and "None" not in text
    assert "Średnia wartość zamówienia: n/d" in text or "wartość zamówienia to n/d" in text


def test_pln_nd_for_non_finite_but_zero_for_tiny_values():
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert pln(bad) == "n/d"
    assert pln(-0.004) == "0,00" and pln(0.0) == "0,00" and pln(None) == "—"


def test_net_zero_revenue_pipeline_has_no_margin(cloud_client, cloud_settings, org):  # noqa: F811
    job, files = v1_run(cloud_client, cloud_settings, org, ZERO)
    assert job["status"] == "succeeded"
    payload = json.loads(files["json"].read_text(encoding="utf-8"))
    m = payload["metrics"]
    assert m["revenue_total"] == 0 and m["profit_total"] == 0
    assert m["margin_pct"] is None and m["avg_order_value"] is None
    assert NEG_WARNING in payload["source_detail"]
    md = files["md"].read_text(encoding="utf-8")
    html = files["html"].read_text(encoding="utf-8")
    assert "MARŻA NIEOKREŚLONA" in md
    assert re.search(r"\| Marża \| n/d \|", md) and "n/d" in html
    assert "| Marża | 0,00 %" not in md and "marża **0,00 %**" not in md
    assert not re.search(r"\b(nan|inf|infinity)\b", md + html + json.dumps(payload), re.IGNORECASE)


def test_only_negative_rows_pipeline(cloud_client, cloud_settings, org):  # noqa: F811
    data = csv_bytes("1,2026-09-01,A,P1,1,-100,-50,0", "2,2026-09-02,A,P1,1,-20,-10,0")
    job, files = v1_run(cloud_client, cloud_settings, org, data)
    m = json.loads(files["json"].read_text(encoding="utf-8"))["metrics"]
    assert job["status"] == "succeeded"
    assert m["revenue_total"] == pytest.approx(-120.0) and m["margin_pct"] is None and m["avg_order_value"] is None


def test_positive_net_revenue_pipeline_shows_the_margin(cloud_client, cloud_settings, org):  # noqa: F811
    _job, files = v1_run(cloud_client, cloud_settings, org, NET)
    md = files["md"].read_text(encoding="utf-8")
    assert "MARŻA ZDROWA" in md and "n/d" not in md.split("## Metryki zbiorcze")[1].split("##")[0]


def test_legacy_net_zero_revenue_has_no_margin(client: TestClient):
    up = client.post("/upload", files={"file": ("k.csv", ZERO, "text/csv")}).json()
    body = client.post("/run", json=up["run_payload"]).json()
    assert body["metrics"]["revenue_total"] == 0
    assert body["metrics"].get("margin_pct") is None
