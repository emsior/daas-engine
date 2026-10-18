"""C6: kwoty i procenty w tabelach raportu w jednej konwencji PL (`7 255,95`, `99,59 %`)."""
from __future__ import annotations

import re

import pytest

from app.core.models import EcommerceMetrics
from app.reporting.fmt import pct, pln
from app.reporting.generator import render_ecommerce_markdown
from app.transforms.ecommerce_transform import transform_ecommerce


def _row(oid, day, cat, prod, revenue, cost, discount=0.0, status="completed"):
    return {"order_id": oid, "date": day, "product": prod, "category": cat, "quantity": 1, "revenue": revenue,
            "cost": cost, "profit": round(revenue - cost, 2), "discount": discount, "status": status}


ROWS = [
    _row("W1-1", "2026-01-05", "Sprzet", "Laptop", 4000.50, 2000.0),
    _row("W1-2", "2026-01-06", "Sprzet", "Mysz", 3255.45, 3300.0, discount=0.3),     # zamowienie stratne
    _row("W2-1", "2026-01-12", "Akcesoria", "Kabel", 1234567.0, 1000000.0),
    _row("W2-2", "2026-01-13", "Sprzet", "Laptop", 99.5, 50.0),
]


@pytest.fixture(scope="module")
def md() -> str:
    metrics = transform_ecommerce(ROWS)
    payload = {"run_id": "r1", "pipeline": "ecommerce_demo", "client_name": None, "data_status": "MOCK_DATA",
               "source_detail": "test", "records_processed": len(ROWS), "generated_at": "2026-01-01 00:00 UTC",
               "narrative": "", "narrative_source": "TEMPLATE"}
    return render_ecommerce_markdown(payload, metrics)


@pytest.mark.parametrize("value,expected", [
    (7255.95, "7 255,95"), (1234567.0, "1 234 567,00"), (0, "0,00"), (-1000.5, "-1 000,50"),
])
def test_pln(value, expected):
    assert pln(value) == expected


@pytest.mark.parametrize("value,nd,expected", [
    (99.59, 2, "99,59 %"), (12.34, 1, "12,3 %"), (0, 1, "0,0 %"), (-0.04, 1, "0,0 %"), (30, 0, "30 %"),
    (None, 1, "n/d"), (float("nan"), 1, "n/d"),
])
def test_pct(value, nd, expected):
    assert pct(value, nd) == expected


def test_signed_pct():
    assert pct(12.34, 1, signed=True) == "+12,3 %" and pct(-5, 1, signed=True) == "-5,0 %"


def test_pct_z_separatorem_tysiecy():
    assert pct(16915.94, 1, signed=True) == "+16 915,9 %"


def test_tabela_tygodni_ma_kwoty_pl(md):
    week2 = next(line for line in md.splitlines() if line.startswith("| 2026-W03"))
    assert "| 1 234 666,50 |" in week2          # przychod
    assert re.search(r"\| \d+(?:,\d)? % \|", week2)    # marza z przecinkiem i spacja przed %
    week1 = next(line for line in md.splitlines() if line.startswith("| 2026-W02"))
    assert "| 7 255,95 |" in week1


def test_tabela_kategorii_i_produktow(md):
    cat = next(line for line in md.splitlines() if line.startswith("| Akcesoria"))
    assert "| 1 234 567,00 |" in cat and "| 234 567,00 |" in cat
    assert re.search(r"\| 18,6 % \|", cat) or re.search(r"\| \d+,\d % \|", cat)
    prod = next(line for line in md.splitlines() if line.startswith("| Kabel"))
    assert "1 234 567,00" in prod


def test_zamowienia_stratne_i_anomalie(md):
    loss = next(line for line in md.splitlines() if line.startswith("| W1-2"))
    assert "| 3 255,45 |" in loss and "| 3 300,00 |" in loss and "| -44,55 |" in loss
    assert "| 30 % |" in loss


def test_wnioski_i_wow_bez_kropek_dziesietnych(md):
    wow = next(line for line in md.splitlines() if line.startswith("**Tydzień"))
    assert not re.search(r"\d\.\d", wow)
    assert re.search(r"[+-]\d[\d ]*,\d %", wow) and " pp" in wow
    assert "99,59" not in md or "99,59 %" in md


def test_brak_kropek_dziesietnych_w_wierszach_tabel(md):
    for line in md.splitlines():
        if line.startswith("| ") and "run_id" not in line and "http" not in line and "week_start" not in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            for c in cells:
                assert not re.fullmatch(r"-?\d+\.\d+", c), line


def test_stala_konwencja_spacji(md):
    assert " " not in md and " " not in md


def test_model_nadal_zwraca_liczby_a_nie_teksty():
    m = transform_ecommerce(ROWS)
    assert isinstance(m, EcommerceMetrics) and isinstance(m.top_categories[0]["revenue"], float)
