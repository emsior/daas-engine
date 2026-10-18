"""Okablowanie języka raportu: RunRequest.language (z Org.locale w executor) dociera do generatora.

Dopina F2 end-to-end — samo `generator.write(language=)` jest testowane w test_report_localization,
tu sprawdzamy, że runner przekazuje `request.language`, więc organizacja `en` dostaje raport EN,
a domyślna (`pl`) raport PL. Patrz test obcego A04 (S-04-02) i okablowanie executor→runner→generator.
"""
from __future__ import annotations

from pathlib import Path

from app.core.models import RunRequest


def _title_line(result) -> str:
    html = Path(result.report_html_path).read_text(encoding="utf-8")
    return html


def test_runner_passes_language_to_the_report(runner):
    pl = runner.run(RunRequest(pipeline="ecommerce_demo", force_mock=True))  # default language="pl"
    en = runner.run(RunRequest(pipeline="ecommerce_demo", force_mock=True, language="en"))

    pl_html = _title_line(pl)
    en_html = _title_line(en)

    assert "Raport sprzedaży e-commerce B2B" in pl_html
    assert "E-Commerce B2B Sales Report" not in pl_html

    assert "E-Commerce B2B Sales Report" in en_html
    assert "Raport sprzedaży e-commerce B2B" not in en_html


def test_default_language_is_pl(runner):
    """Brak jawnego języka = PL (naprawa A04: polska właścicielka nie dostaje angielskiego raportu)."""
    result = runner.run(RunRequest(pipeline="ecommerce_demo", force_mock=True))
    assert RunRequest(pipeline="ecommerce_demo").language == "pl"
    assert "Raport sprzedaży e-commerce B2B" in _title_line(result)
