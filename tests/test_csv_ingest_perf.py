"""Ingest CSV: rownowaznosc po optymalizacji + bramka wydajnosciowa.

Sprint perf/pandas-pipeline. Optymalizacja dotknela trzech miejsc goracej sciezki:
odczytu (silnik C zamiast `sep=None`+python), konwersji liczb i normalizacji statusu.
Te testy pilnuja, ze zaden z tych kroków nie zmienil wyniku — przyspieszenie, ktore
gubi dane albo omija walidacje, nie jest przyspieszeniem.

Wszystkie dane sa generowane w locie. Bez sieci, bez sekretow, bez plikow klienta.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

from app.sources.csv_mapper import (
    STATUS_MAP,
    _norm,
    _to_number,
    detect_mapping,
    map_dataframe,
    normalize_status_series,
    parse_dates,
    read_client_csv,
    to_number_series,
)

# ---------------------------------------------------------------------------
# Rownowaznosc: wektor vs referencja skalarna
# ---------------------------------------------------------------------------

#: Materiał celowo obrzydliwy — tak wyglądają eksporty z Allegro, Excela i WooCommerce.
NUMBER_CASES = [
    "1 299,00 zł", "1299.00", "1299,00", "1.299,00", "1,299.00", "12.5%", "0",
    "0,0", "-45,50", "-45.50", "  7 255,95  ", "1 000 000,01", "49", "49.0",
    "", "   ", "nan", "NaN", "None", "null", "-", "abc", "zł", "12abc34",
    "3,14", "3.14", "1.234.567,89", "1,234,567.89", "0%", "100%", "0.5",
    ".5", "5.", ",5", "5,", "--3", "1-2", "+7", "7 ", " 7",
]


def test_to_number_series_rownowazne_ze_skalarem():
    """Wektor musi dawac dokladnie to, co stara sciezka `.map(_to_number)`."""
    col = pd.Series(NUMBER_CASES + [None], dtype=object)
    expected = col.map(_to_number).astype("float64")
    got = to_number_series(col)
    pd.testing.assert_series_equal(got, expected, check_names=False)


def test_to_number_series_kolumna_juz_liczbowa():
    col = pd.Series([1, 2, 3, None], dtype="float64")
    got = to_number_series(col)
    assert got.tolist()[:3] == [1.0, 2.0, 3.0]
    assert pd.isna(got.iloc[3])


@pytest.mark.parametrize("raw", NUMBER_CASES)
def test_szybka_sciezka_nie_zmienia_wyniku_pojedynczej_wartosci(raw):
    """Fast path w `_to_number` liczy sie tylko, jesli nie zmienia zadnej wartosci."""
    vect = to_number_series(pd.Series([raw], dtype=object)).iloc[0]
    scalar = _to_number(raw)
    if scalar is None:
        assert pd.isna(vect)
    else:
        assert vect == pytest.approx(scalar)


def test_wykladnik_nie_jest_traktowany_jak_liczba():
    """Regresja: `float('1e5')` to 100000, ale kontrakt mappera daje 15.0.

    Fast path nie moze przechwycic notacji wykladniczej, bo zmienilby wynik.
    """
    assert _to_number("1e5") == 15.0
    assert to_number_series(pd.Series(["1e5"], dtype=object)).iloc[0] == 15.0


def test_normalize_status_series_rownowazne_ze_stara_petla():
    statuses = ["Zrealizowane", "zwrot", "ANULOWANE", "Oczekujące", "shipped",
                "dziwny status", "", None, "Zrealizowane", "refund"]
    col = pd.Series(statuses, dtype=object)
    expected = col.fillna("completed").astype(str).map(
        lambda s: STATUS_MAP.get(_norm(s), "completed")
    )
    got = normalize_status_series(col)
    assert got.tolist() == expected.tolist()


# ---------------------------------------------------------------------------
# Daty: jawny format zamiast heurystyki
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "values,expected_first",
    [
        (["2026-03-01", "2026-03-02"], "2026-03-01"),
        (["01.03.2026", "02.03.2026"], "2026-03-01"),
        (["01/03/2026", "02/03/2026"], "2026-03-01"),
        (["2026-03-01 10:15:00", "2026-03-02 11:00:00"], "2026-03-01"),
    ],
)
def test_parse_dates_formaty(values, expected_first):
    got = parse_dates(pd.Series(values, dtype=object))
    assert str(got.iloc[0].date()) == expected_first
    assert got.notna().all()


def test_parse_dates_dzien_przed_miesiacem_bez_dwuznacznosci():
    """`13.01.2026` moze byc tylko 13 stycznia — nie 1 trzynastego."""
    got = parse_dates(pd.Series(["13.01.2026"], dtype=object))
    assert str(got.iloc[0].date()) == "2026-01-13"


def test_parse_dates_bledne_daty_daja_nat():
    got = parse_dates(pd.Series(["2026-03-01", "nie-data", "", "32.13.2026"], dtype=object))
    assert got.notna().sum() == 1
    assert got.isna().sum() == 3


def test_parse_dates_format_mieszany_nadal_dziala():
    got = parse_dates(pd.Series(["2026-03-01", "02.03.2026"], dtype=object))
    assert got.notna().all()


def test_parsowanie_dat_nie_generuje_ostrzezenia(recwarn):
    """Stara sciezka krzyczala UserWarning o `dayfirst` przy kazdym uruchomieniu."""
    parse_dates(pd.Series(["2026-03-01"] * 5, dtype=object))
    assert [w for w in recwarn if issubclass(w.category, UserWarning)] == []


# ---------------------------------------------------------------------------
# Odczyt pliku: separator i kodowanie
# ---------------------------------------------------------------------------
HEADER_PL = "Data;Nr zamowienia;Produkt;Kwota;Koszt;Status"
HEADER_EN = "date,order_id,product,revenue,cost,status"


def _write(tmp_path: Path, name: str, text: str, encoding: str = "utf-8") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding=encoding, newline="")
    return p


@pytest.mark.parametrize(
    "header,row,sep",
    [
        (HEADER_EN, "2026-03-01,ORD-1,Rzecz,100.00,60.00,completed", ","),
        (HEADER_PL, "01.03.2026;ZAM-1;Rzecz;100,00 zl;60,00 zl;Zrealizowane", ";"),
        ("date\torder_id\tproduct\trevenue\tcost\tstatus",
         "2026-03-01\tORD-1\tRzecz\t100.00\t60.00\tcompleted", "\t"),
    ],
)
def test_read_client_csv_rozpoznaje_separator(tmp_path, header, row, sep):
    p = _write(tmp_path, "in.csv", f"{header}\n{row}\n{row}\n")
    df = read_client_csv(str(p))
    assert len(df) == 2
    assert len(df.columns) == 6


def test_read_client_csv_cp1250(tmp_path):
    p = _write(tmp_path, "cp.csv", f"{HEADER_PL}\n01.03.2026;ZAM-1;Łódź;100,00;60,00;Zrealizowane\n",
               encoding="cp1250")
    df = read_client_csv(str(p))
    assert len(df) == 1


def test_read_client_csv_odrzuca_zepsute_kodowanie(tmp_path):
    """Bajty nieprzypisane w cp1250 (0x81, 0x83, 0x90) musza konczyc sie odmowa.

    Uwaga: cp1250 dekoduje wiekszosc bajtow, wiec nie kazdy smiec binarny zostanie
    odrzucony — to zachowanie sprzed tego sprintu i nie zmienia sie tutaj.
    """
    p = tmp_path / "bin.csv"
    p.write_bytes(b"Data;Kwota\n\x81\x83\x90;10\n")
    with pytest.raises(UnicodeDecodeError):
        read_client_csv(str(p))


# ---------------------------------------------------------------------------
# Brak regresji mapowania: PL i EN
# ---------------------------------------------------------------------------
def test_mapping_pl_bez_regresji(tmp_path):
    rows = "\n".join(
        f"0{d}.03.2026;ZAM-{d};Rzecz {d};Peryferia;{d};1 {d}99,00 zl;{d}00,00 zl;10%;Zrealizowane"
        for d in range(1, 6)
    )
    p = _write(tmp_path, "pl.csv",
               "Data;Nr zamowienia;Produkt;Kategoria;Ilosc;Kwota brutto;Koszt zakupu;Rabat;Status\n" + rows + "\n")
    mapped, rep = map_dataframe(read_client_csv(str(p)))
    assert rep.rows_out == 5
    assert set(["order_id", "date", "product", "revenue", "cost", "status"]) <= set(rep.mapping)
    assert mapped["revenue"].iloc[0] == pytest.approx(1199.0)
    assert mapped["cost"].iloc[0] == pytest.approx(100.0)
    assert mapped["discount"].iloc[0] == pytest.approx(0.10)
    assert mapped["status"].unique().tolist() == ["completed"]
    assert str(mapped["date"].iloc[0]) == "2026-03-01"


def test_mapping_en_bez_regresji(tmp_path):
    rows = "\n".join(
        f"2026-03-0{d},ORD-{d},Thing {d},Peripherals,{d},1{d}99.00,{d}00.00,0.1,completed"
        for d in range(1, 6)
    )
    p = _write(tmp_path, "en.csv",
               "date,order_id,product,category,quantity,revenue,cost,discount,status\n" + rows + "\n")
    mapped, rep = map_dataframe(read_client_csv(str(p)))
    assert rep.rows_out == 5
    assert mapped["revenue"].iloc[0] == pytest.approx(1199.0)
    assert mapped["status"].unique().tolist() == ["completed"]
    assert str(mapped["date"].iloc[0]) == "2026-03-01"


def test_brak_wymaganej_kolumny_nadal_konczy_sie_bledem(tmp_path):
    p = _write(tmp_path, "zle.csv", "kolumnaa;kolumnab\nfoo;bar\n")
    with pytest.raises(ValueError):
        map_dataframe(read_client_csv(str(p)))


def test_wiersze_z_bledna_data_sa_pomijane_z_ostrzezeniem(tmp_path):
    p = _write(tmp_path, "mix.csv",
               "date,order_id,revenue\n2026-03-01,A,100\nnie-data,B,200\n2026-03-02,C,300\n")
    mapped, rep = map_dataframe(read_client_csv(str(p)))
    assert rep.rows_out == 2
    assert any("nieczyteln" in w for w in rep.warnings)


# ---------------------------------------------------------------------------
# Bramka wydajnosciowa
# ---------------------------------------------------------------------------
#: Prog swiadomie luzny. Na maszynie deweloperskiej odczyt+mapowanie 50k wierszy
#: schodzi ponizej 1 s; prog 12 s lapie regresje rzedu wielkosci (np. powrot do
#: `engine="python"` albo `_norm` na kazdy wiersz), a nie wahania obciazenia CI.
PERF_ROWS = 50_000
PERF_BUDGET_S = 12.0


@pytest.mark.performance
def test_ingest_50k_wierszy_miesci_sie_w_budzecie(tmp_path):
    lines = ["date,order_id,product,category,quantity,revenue,cost,discount,status"]
    for i in range(PERF_ROWS):
        lines.append(
            f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d},ORD-{i:07d},Rzecz {i % 50},"
            f"Kat {i % 5},{(i % 9) + 1},{100 + i % 4000}.{i % 100:02d},"
            f"{50 + i % 2000}.00,{i % 20},completed"
        )
    p = tmp_path / "perf.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")

    t0 = time.perf_counter()
    mapped, rep = map_dataframe(read_client_csv(str(p)))
    elapsed = time.perf_counter() - t0

    # Poprawnosc przed szybkoscia — inaczej test nagradza pomijanie pracy.
    assert rep.rows_out == PERF_ROWS
    assert mapped["revenue"].notna().all()
    assert mapped["date"].notna().all()
    assert elapsed < PERF_BUDGET_S, (
        f"ingest {PERF_ROWS} wierszy zajal {elapsed:.2f}s, budzet {PERF_BUDGET_S}s — "
        "sprawdz, czy ktoras operacja nie wrocila do petli per wiersz"
    )
