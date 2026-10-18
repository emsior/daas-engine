"""C2: wiersze z ujemnym przychodem to korekty/zwroty — nie znikaja po cichu."""
from __future__ import annotations

import pandas as pd
import pytest

from app.sources.csv_mapper import map_dataframe
from app.transforms.ecommerce_transform import transform_ecommerce

NEG_WARNING = "ujemnym przychodem (korekty/zwroty) — uwzględnione"


def _frame(revenues: list[str], costs: list[str] | None = None) -> pd.DataFrame:
    data = {
        "data": [f"2026-01-{5 + i:02d}" for i in range(len(revenues))],
        "numer": [f"N{i}" for i in range(len(revenues))],
        "kwota": revenues,
    }
    if costs is not None:
        data["koszt"] = costs
    return pd.DataFrame(data)


@pytest.mark.parametrize("revenues,negatives", [
    (["-100", "0", "50"], 1),
    (["-100", "-20,50", "50"], 2),
    (["-10", "-20"], 2),                       # plik z samymi ujemnymi
])
def test_ujemny_przychod_zostaje_w_rekordach_z_ostrzezeniem(revenues, negatives):
    out, rep = map_dataframe(_frame(revenues))
    assert rep.rows_in == len(revenues) and rep.rows_out == len(revenues)
    assert len(out) == len(revenues)
    assert int((out["revenue"] < 0).sum()) == negatives
    assert f"{negatives} wierszy z {NEG_WARNING}" in rep.warnings


def test_brak_ujemnych_nie_dodaje_ostrzezenia():
    _, rep = map_dataframe(_frame(["100", "0", "50"]))
    assert not any("ujemnym" in w for w in rep.warnings)


def test_metryki_sumuja_korekty():
    out, _ = map_dataframe(_frame(["100", "-30", "50"], ["40", "0", "20"]))
    m = transform_ecommerce(out.to_dict(orient="records"))
    assert m.orders_total == 3
    assert m.revenue_total == pytest.approx(120.0)
    assert m.cost_total == pytest.approx(60.0)
    assert m.profit_total == pytest.approx(60.0)              # 60 + (-30) + 30
    assert sum(w["revenue"] for w in m.weekly) == pytest.approx(m.revenue_total)


def test_korekta_nie_jest_zamowieniem_stratnym():
    """Zwrot (-30, koszt 0) obniza przychod i zysk, ale nie jest 'zamowieniem stratnym' do blokowania rabatow."""
    out, _ = map_dataframe(_frame(["100", "-30", "50"], ["40", "0", "20"]))
    m = transform_ecommerce(out.to_dict(orient="records"))
    assert m.loss_orders_count == 0
    assert all(a["revenue"] >= 0 for a in m.anomalies)


def test_same_ujemne_daja_skonczone_metryki():
    out, _ = map_dataframe(_frame(["-10", "-20"]))
    m = transform_ecommerce(out.to_dict(orient="records"))
    assert m.revenue_total == pytest.approx(-30.0)
    assert m.profit_total == pytest.approx(-30.0)
