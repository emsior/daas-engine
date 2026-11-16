"""Stopka raportu HTML opisuje, co naprawde liczy liczby: transformacje w Pythonie, nie DuckDB."""
from __future__ import annotations

import html

from app.reporting.html import FOOTER, render_html


def _render() -> str:
    return render_html("# Raport\n\ntekst\n", title="t", data_status="MOCK_DATA",
                       narrative=None, narrative_source=None)


def test_footer_names_python_and_duckdb_storage():
    out = _render()
    assert f'<div class="foot">{html.escape(FOOTER)}</div>' in out
    assert "liczby policzone w Pythonie z rekordów źródłowych" in out
    assert "historia runów zapisane w DuckDB" in out


def test_footer_drops_the_old_duckdb_claim():
    out = _render()
    assert "liczby z DuckDB" not in out
    assert "narracja opisowa" not in out


def test_footer_is_the_same_with_a_narrative():
    out = render_html("# x\n", title="t", data_status="LIVE_DATA", narrative="Przychód wzrósł.",
                      narrative_source="TEMPLATE")
    assert out.count('<div class="foot">') == 1
    assert "liczby z DuckDB" not in out
