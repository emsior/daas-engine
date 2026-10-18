"""C14: formuly XLSX bez zbuforowanych wartosci sa wykrywane i opisane ostrzezeniem."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import openpyxl

from app.sources.xlsx_formulas import FORMULA_WARNING, count_formulas_without_value, formula_warning


def _workbook(path: Path, rows: list[list], second_sheet: bool = False) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sprzedaz"
    for row in rows:
        ws.append(row)
    if second_sheet:
        wb.create_sheet("Inne").append(["x", "=1+1"])
    wb.save(path)
    return path


def _with_cached_values(src: Path, dst: Path) -> Path:
    """To samo, ale z wartosciami przy formulach (jak po zapisie w Excelu): `<v></v>` -> `<v>6</v>`."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.startswith("xl/worksheets/"):
                data = data.replace(b"<v></v>", b"<v>6</v>").replace(b"<v />", b"<v>6</v>")
            zout.writestr(item, data)
    return dst


HEADER = ["data", "numer", "ilosc", "cena", "kwota"]


def test_formuly_bez_wartosci_sa_liczone(tmp_path):
    path = _workbook(tmp_path / "a.xlsx", [HEADER, ["2026-01-05", "A1", 2, 3, "=C2*D2"], ["2026-01-06", "A2", 4, 5, "=C3*D3"]])
    assert count_formulas_without_value(path) == 2
    warning = formula_warning(path)
    assert warning == FORMULA_WARNING.format(n=2)
    assert "zapisz plik ponownie w Excelu/LibreOffice" in warning


def test_formuly_z_wartosciami_nie_ostrzegaja(tmp_path):
    src = _workbook(tmp_path / "a.xlsx", [HEADER, ["2026-01-05", "A1", 2, 3, "=C2*D2"]])
    cached = _with_cached_values(src, tmp_path / "b.xlsx")
    assert count_formulas_without_value(cached) == 0 and formula_warning(cached) is None


def test_zwykle_wartosci_nie_ostrzegaja(tmp_path):
    plain = _workbook(tmp_path / "p.xlsx", [HEADER, ["2026-01-05", "A1", 2, 3, 6], ["x = y", "A2", 1, 1, 1]])
    assert count_formulas_without_value(plain) == 0 and formula_warning(plain) is None


def test_liczy_tylko_wskazany_arkusz(tmp_path):
    path = _workbook(tmp_path / "a.xlsx", [HEADER, ["2026-01-05", "A1", 2, 3, 6]], second_sheet=True)
    assert count_formulas_without_value(path, "Sprzedaz") == 0
    assert count_formulas_without_value(path, "Inne") == 1


def test_uszkodzony_plik_to_zero_i_nie_rzuca(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"to nie jest zip")
    assert count_formulas_without_value(bad) == 0
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("foo.txt", "x")
    other = tmp_path / "other.xlsx"
    other.write_bytes(buf.getvalue())
    assert formula_warning(other) is None


def test_nieistniejacy_arkusz_to_zero(tmp_path):
    path = _workbook(tmp_path / "a.xlsx", [HEADER, ["2026-01-05", "A1", 2, 3, "=C2*D2"]])
    assert count_formulas_without_value(path, "Brak") == 0
