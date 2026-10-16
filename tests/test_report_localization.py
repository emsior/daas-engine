"""F2 (A04 S-04-02): lokalizacja raportu — przy jezyku `pl` tytul i naglowki tabel po polsku, przy `en` po angielsku.

Tresc sekcji (WNIOSEK, nazwy sekcji, PRAKTYCZNE WNIOSKI) jest juz po polsku i wspolna dla obu jezykow;
i18n obejmuje TYLKO tytul raportu i naglowki kolumn tabel. Domyslnie `pl` (domyslne locale organizacji = `pl`);
`en` dostaje angielski po przekazaniu language=org.locale przez runner (patrz flaga dowodcy w raporcie PR).
"""
from __future__ import annotations

from datetime import datetime

from app.core.models import DataStatus
from app.reporting.generator import (
    ReportGenerator,
    _headers_for,
    render_ecommerce_markdown,
    report_title,
)
from app.transforms.ecommerce_transform import transform_ecommerce


def _row(oid, day, cat, prod, revenue, cost, discount=0.0, status="completed"):
    return {"order_id": oid, "date": day, "product": prod, "category": cat, "quantity": 1, "revenue": revenue,
            "cost": cost, "profit": round(revenue - cost, 2), "discount": discount, "status": status}


# dwa tygodnie ISO (W02 + W03) -> tabela "Tydzien do tygodnia" + wow sa obecne
ROWS = [
    _row("W1-1", "2026-01-05", "Sprzet", "Laptop", 4000.50, 2000.0),
    _row("W1-2", "2026-01-06", "Sprzet", "Mysz", 3255.45, 3300.0, discount=0.3),
    _row("W2-1", "2026-01-12", "Akcesoria", "Kabel", 12345.0, 10000.0),
    _row("W2-2", "2026-01-13", "Sprzet", "Laptop", 99.5, 50.0),
]


def _payload() -> dict:
    return {"run_id": "r1", "pipeline": "ecommerce_demo", "client_name": None, "data_status": "MOCK_DATA",
            "source_detail": "test", "records_processed": len(ROWS), "generated_at": "2026-01-01 00:00 UTC",
            "narrative": "", "narrative_source": "TEMPLATE"}


# --------------------------------------------------------------- helpery tytulu / naglowkow
def test_report_title_pl_and_en():
    assert report_title("ecommerce_demo", "pl") == "Raport sprzedaży e-commerce B2B"
    assert report_title("ecommerce_demo", "en") == "E-Commerce B2B Sales Report"
    assert report_title("cs2_demo", "pl") == "Raport statystyk CS2"
    assert report_title("cs2_demo", "en") == "CS2 Performance Report"


def test_headers_for_pl_translates_keys_en_keeps_them():
    cols = ["week", "revenue", "profit", "margin_pct", "aov"]
    assert _headers_for(cols, "pl") == ["tydzień", "przychód", "zysk", "marża", "śr. wartość"]
    # en: None = uzyj kluczy jako naglowkow (zachowanie sprzed F2)
    assert _headers_for(cols, "en") is None
    # klucz spoza mapy zostaje bez tlumaczenia
    assert _headers_for(["run_id"], "pl") == ["run_id"]


# --------------------------------------------------------------- markdown ecommerce
def test_ecommerce_markdown_pl_has_polish_title_and_table_headers():
    md = render_ecommerce_markdown(_payload(), transform_ecommerce(ROWS), language="pl")
    assert md.startswith("# Raport sprzedaży e-commerce B2B")
    assert "| przychód | zysk | marża | śr. wartość |" in md        # tabela tydzien-do-tygodnia
    assert "| kategoria | zamówienia | przychód |" in md            # top kategorie
    # angielskie klucze nie pojawiaja sie jako naglowki kolumn
    assert "| revenue | profit |" not in md and "| margin_pct |" not in md


def test_ecommerce_markdown_en_keeps_english_title_and_table_headers():
    md = render_ecommerce_markdown(_payload(), transform_ecommerce(ROWS), language="en")
    assert md.startswith("# E-Commerce B2B Sales Report")
    assert "| revenue | profit | margin_pct | aov |" in md
    # polskie naglowki kolumn nie wchodza do wersji angielskiej
    assert "| przychód | zysk |" not in md


# --------------------------------------------------------------- HTML przez write()
def _write_html(tmp_path, language=None) -> str:
    gen = ReportGenerator(tmp_path / "rep")
    kw = {} if language is None else {"language": language}
    rep = gen.write("ecommerce_demo", "abcdef0123456789", DataStatus.MOCK_DATA, transform_ecommerce(ROWS),
                    "test", len(ROWS), datetime(2026, 1, 1), client_name="Sklep", **kw)
    return rep.html.read_text(encoding="utf-8")


def test_write_default_language_is_polish(tmp_path):
    """Bez argumentu `language` raport jest po polsku — naprawia A04 dla domyslnego locale organizacji (`pl`)."""
    html = _write_html(tmp_path)
    assert "<title>Raport sprzedaży e-commerce B2B</title>" in html
    assert "E-Commerce B2B Sales Report" not in html          # brak angielskiego tytulu
    assert ">przychód<" in html and ">zysk<" in html          # naglowki kolumn po polsku


def test_write_pl_html_localizes_category_and_product_headers(tmp_path):
    html = _write_html(tmp_path, language="pl")
    assert "<title>Raport sprzedaży e-commerce B2B</title>" in html
    assert ">kategoria<" in html and ">produkt<" in html


def test_write_en_keeps_english_title_and_headers(tmp_path):
    html = _write_html(tmp_path, language="en")
    assert "<title>E-Commerce B2B Sales Report</title>" in html
    assert "Raport sprzedaży e-commerce B2B" not in html      # brak polskiego tytulu
    assert ">revenue<" in html and ">profit<" in html
