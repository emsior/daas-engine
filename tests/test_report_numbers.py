"""C4: dzielenie przez zero, NaN/inf nigdy nie trafiaja do JSON, Markdown ani narracji."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from app.core.models import RunRequest, RunStatus
from app.reporting.generator import _pln
from app.reporting.narrative import _pln as narrative_pln
from app.reporting.narrative import template_narrative
from app.transforms.ecommerce_transform import transform_ecommerce

ZERO_REVENUE_CSV = (
    "data;numer;kategoria;produkt;kwota;koszt\n"
    "2026-01-05;A1;Bonusy;Gratis;0;10\n"
    "2026-01-06;A2;Bonusy;Gratis 2;0;5\n"
    "2026-01-07;B1;Sprzet;Mysz;100;40\n"
)


def _no_constants(name: str):
    raise AssertionError(f"niedozwolona stala JSON: {name}")


def _strict_loads(text: str):
    return json.loads(text, parse_constant=_no_constants)


def _walk(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)
    else:
        yield node


@pytest.mark.parametrize("fn", [_pln, narrative_pln])
@pytest.mark.parametrize("value,expected", [
    (float("nan"), "0,00"), (float("inf"), "0,00"), (float("-inf"), "0,00"),
    (-0.004, "0,00"), (-0.0, "0,00"), (0.004, "0,00"), (7255.95, "7 255,95"), (-1000.5, "-1 000,50"),
    (0, "0,00"), (1234567.891, "1 234 567,89"),
])
def test_pln_nie_pokazuje_nan_ani_minus_zera(fn, value, expected):
    assert fn(value) == expected


def test_pln_none_to_kreska():
    assert _pln(None) == "—"


def test_marza_kategorii_przy_przychodzie_zero_to_none():
    rows = [
        {"order_id": "A1", "date": "2026-01-05", "product": "Gratis", "category": "Bonusy", "quantity": 1,
         "revenue": 0.0, "cost": 10.0, "profit": -10.0, "discount": 0.0, "status": "completed"},
        {"order_id": "B1", "date": "2026-01-07", "product": "Mysz", "category": "Sprzet", "quantity": 1,
         "revenue": 100.0, "cost": 40.0, "profit": 60.0, "discount": 0.0, "status": "completed"},
    ]
    m = transform_ecommerce(rows)
    by = {c["category"]: c for c in m.top_categories}
    assert by["Bonusy"]["margin_pct"] is None
    assert by["Sprzet"]["margin_pct"] == 60.0
    for value in _walk(m.model_dump()):
        if isinstance(value, float):
            assert math.isfinite(value)


def _run(runner, tmp_path: Path):
    uploads = runner.settings.reports_path.parent / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    dest = uploads / "zero.csv"
    dest.write_text(ZERO_REVENUE_CSV, encoding="utf-8")
    res = runner.run(RunRequest(pipeline="ecommerce_demo", source_path=str(dest), client_name="Sklep"))
    assert res.run_status == RunStatus.SUCCESS, res.error
    return res


def test_raport_z_zerowym_przychodem_jest_poprawnym_json(runner, tmp_path):
    res = _run(runner, tmp_path)
    text = Path(res.report_json_path).read_text(encoding="utf-8")
    payload = _strict_loads(text)
    for value in _walk(payload):
        if isinstance(value, float):
            assert math.isfinite(value)
    cats = {c["category"]: c for c in payload["metrics"]["top_categories"]}
    assert cats["Bonusy"]["margin_pct"] is None


def test_markdown_html_i_narracja_bez_nan(runner, tmp_path):
    res = _run(runner, tmp_path)
    bad = re.compile(r"\b(nan|inf|infinity)\b", re.IGNORECASE)
    for path in (res.report_path, res.report_html_path, res.report_json_path):
        assert not bad.search(Path(path).read_text(encoding="utf-8")), path
    payload = _strict_loads(Path(res.report_json_path).read_text(encoding="utf-8"))
    assert not bad.search(payload["narrative"])
    assert "n/d" in Path(res.report_path).read_text(encoding="utf-8")


def test_narracja_szablonowa_z_pusta_marza_kategorii():
    rows = [{"order_id": "A1", "date": "2026-01-05", "product": "Gratis", "category": "Bonusy", "quantity": 1,
             "revenue": 0.0, "cost": 10.0, "profit": -10.0, "discount": 0.0, "status": "completed"}]
    text = template_narrative("ecommerce_demo", transform_ecommerce(rows))
    assert "None" not in text and "nan" not in text.lower()


def test_generator_odrzuca_nan_w_json_ale_sanityzuje_metryki(tmp_path):
    from datetime import datetime

    from app.core.models import DataStatus
    from app.reporting.generator import ReportGenerator

    gen = ReportGenerator(tmp_path / "r")
    rep = gen.write("custom_x", "abcdef123456", DataStatus.MOCK_DATA,
                    {"ratio": float("nan"), "nested": [{"v": float("inf")}], "ok": 1.5},
                    "test", 1, datetime(2026, 1, 1))
    payload = _strict_loads(rep.json.read_text(encoding="utf-8"))
    assert payload["metrics"] == {"ratio": None, "nested": [{"v": None}], "ok": 1.5}
