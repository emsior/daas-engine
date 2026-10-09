"""Bezpieczne wstawianie danych klienta do raportow Markdown/HTML.

Dwie warstwy, obie obowiazkowe:

1. Wejscie do Markdown: kazda wartosc pochodzaca z CSV, paczki albo zewnetrznego API przechodzi przez
   `md_text()` (tekst/komorka tabeli) albo `md_code()` (wnetrze znacznika kodu). HTML jest escapowany,
   a znaki Markdown (`[`, `]`, `*`, `_`, `` ` ``, `|`, `!`, `\\`) dostaja backslash — wartosc nie stworzy
   linku, obrazka ani nowej komorki.
2. Render Markdown -> HTML: `render_markdown()` wylacza surowy HTML oraz wszystkie linki, obrazki
   i autolinki. Raport nie zawiera odnosnikow, wiec nic nie traci; wartosc, ktorej pominieto warstwe 1,
   nadal nie stanie sie znacznikiem.
"""
from __future__ import annotations

import html
import re
from typing import Any

import markdown
from markdown.extensions import Extension

_MD_SPECIALS = re.compile(r"([\\`*_\[\]|!])")
_WS = re.compile(r"[\r\n\t\v\f  ]+")


def md_text(value: Any) -> str:
    """Tekst bezpieczny do wstawienia w akapit, naglowek albo komorke tabeli Markdown.

    Wartosci nie-tekstowe (liczby, daty) zwraca przez `str()` bez zmian: nie niosa znacznikow,
    a formatowanie liczb zostaje czytelne.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    text = _WS.sub(" ", value)
    return _MD_SPECIALS.sub(r"\\\1", html.escape(text, quote=False))


def md_code(value: Any) -> str:
    """Tekst do wnetrza znacznika kodu (`...`). Backtick zamieniony na apostrof, zeby nie zamknac spanu.

    Zawartosc spanu kodu jest escapowana do HTML przez renderer Markdown.
    """
    if value is None:
        return ""
    return _WS.sub(" ", str(value)).replace("`", "'")


class _ReportMarkdown(Extension):
    """Wylacza surowy HTML, linki, obrazki i autolinki w Python-Markdown."""

    _INLINE = ("html", "reference", "link", "image_link", "image_reference", "short_reference",
               "short_image_ref", "autolink", "automail")

    def extendMarkdown(self, md: markdown.Markdown) -> None:  # noqa: N802  (nazwa z API biblioteki)
        md.preprocessors.deregister("html_block")
        for name in self._INLINE:
            md.inlinePatterns.deregister(name)


def render_markdown(md_text_: str) -> str:
    """Markdown -> HTML bez surowego HTML i bez linkow (patrz docstring modulu)."""
    return markdown.markdown(md_text_, extensions=["tables", "fenced_code", _ReportMarkdown()])
