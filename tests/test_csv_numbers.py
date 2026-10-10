"""C3: parsowanie liczb — format ksiegowy, U+2212, notacja naukowa; niejednoznaczne -> None + ostrzezenie."""
from __future__ import annotations

import pandas as pd
import pytest

from app.sources.csv_mapper import _to_number, map_dataframe, to_number_series


@pytest.mark.parametrize("raw,expected", [
    # format ksiegowy i znak minus z Unicode
    ("(100)", -100.0), ("(1 299,00)", -1299.0), ("(100,50) zł", -100.5), ("( 7 )", -7.0),
    ("−100", -100.0), ("−1 000,50", -1000.5), ("−$5.00", -5.0),
    # notacja naukowa
    ("1.2E+06", 1200000.0), ("1e5", 100000.0), ("1,5e3", 1500.0), ("-2E-2", -0.02), ("3E0", 3.0),
    # formaty PL / EN
    ("1,299", 1.299), ("1.234.567", 1234567.0), ("7 255,95", 7255.95), ("-1 000,50", -1000.5),
    ("$1,299.00", 1299.0), ("1 299,00 zł", 1299.0), ("1.299,00", 1299.0), ("1,299.00", 1299.0),
    ("1.234.567,89", 1234567.89), ("1,234,567.89", 1234567.89), ("1,234,567", 1234567.0),
    ("7 255,95", 7255.95), ("7 255,95", 7255.95), ("PLN 100", 100.0), ("100 PLN", 100.0),
    ("12.5%", 12.5), ("-41", -41.0), ("  0,30 ", 0.3), ("+7", 7.0), (".5", 0.5), (",5", 0.5), ("5.", 5.0),
    ("0.500", 0.5), (7, 7.0), (-3.5, -3.5),
])
def test_parsowanie_poprawnych_liczb(raw, expected):
    assert _to_number(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [
    "", "  ", "nan", "NaN", "None", "null", "-", "abc", "zł",
    "12abc34",            # smieci miedzy cyframi: dawniej 1234 (zmiana rzedu wielkosci)
    "1-2", "--3", "100-", "(-100)", "-(100)",   # niejednoznaczny znak
    "1.2.3", "1,2,3", "1.23.456", "1 23", "1 2345", "1,299.00,5", "12 345.6 7",
    "1e999", "inf", "-inf", "Infinity", "0x10", "1e", "E5", "1e5e5", "1.2E+06E",
    float("inf"), float("-inf"), float("nan"),
])
def test_niejednoznaczne_to_none_nigdy_inna_liczba(raw):
    assert _to_number(raw) is None


def test_serie_zgodna_ze_skalarem_dla_nowych_formatow():
    raws = ["(100)", "−100", "1.2E+06", "1e5", "1,299", "1.234.567", "abc", "12abc34", None]
    col = pd.Series(raws, dtype=object)
    expected = col.map(_to_number).astype("float64")
    pd.testing.assert_series_equal(to_number_series(col), expected, check_names=False)


def _frame(**cols) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    return pd.DataFrame({"data": [f"2026-01-{5 + i:02d}" for i in range(n)], "numer": [f"N{i}" for i in range(n)], **cols})


def test_nieczytelny_przychod_pomijany_z_ostrzezeniem_o_formacie():
    out, rep = map_dataframe(_frame(kwota=["100", "12abc34", "(50)"]))
    assert out["revenue"].tolist() == [100.0, -50.0]
    assert any("12abc34" not in w and "kwota" in w and "nie da się" in w for w in rep.warnings)
    assert any("1 wierszy" in w for w in rep.warnings)          # pominiecie wiersza bez przychodu


def test_nieczytelny_koszt_nie_jest_cicho_zerem():
    out, rep = map_dataframe(_frame(kwota=["100", "200"], koszt=["40", "dwadziescia"]))
    assert out["cost"].tolist() == [40.0, 0.0]
    assert any("koszt" in w and "nie da się" in w and "1" in w for w in rep.warnings)


def test_znak_i_rzad_wielkosci_zachowane_end_to_end():
    out, _ = map_dataframe(_frame(kwota=["(100)", "−200", "1.2E+06", "1e5"]))
    assert out["revenue"].tolist() == [-100.0, -200.0, 1200000.0, 100000.0]
