"""Generator raportów JSON + Markdown do runtime/reports/."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.models import CS2Metrics, DataStatus, EcommerceMetrics, UkscMetrics
from app.reporting.fmt import pct, pln, pp, sanitize_floats
from app.reporting.html import render_html
from app.reporting.narrative import build_narrative, source_label
from app.reporting.safe import md_text

#: Jezyk raportu (tytul + naglowki tabel). Tresc sekcji (WNIOSEK, PRAKTYCZNE WNIOSKI, nazwy sekcji) jest juz po
#: polsku i jest wspolna dla obu jezykow — i18n obejmuje wylacznie tytul i naglowki kolumn (A04 S-04-02). UKSC ma
#: wlasny raport (uksc_report.py, PL) i nie korzysta z tych map. Domyslnie `pl`: domyslne locale organizacji to
#: `pl` (Org.locale), a raport dla `en` wymaga przekazania language=org.locale przez runner (patrz flaga w PR).
DEFAULT_LANGUAGE = "pl"

#: Tytul raportu wg pipeline'u i jezyka. Brak jezyka -> EN; brak pipeline'u -> `Report <pipeline>`.
_TITLES: dict[str, dict[str, str]] = {
    "cs2_demo": {"pl": "Raport statystyk CS2", "en": "CS2 Performance Report"},
    "ecommerce_demo": {"pl": "Raport sprzedaży e-commerce B2B", "en": "E-Commerce B2B Sales Report"},
}

#: Etykieta naglowka kolumny (PL) wg klucza danych wiersza. Klucz pozostaje nazwa pola w danych — zmieniamy
#: TYLKO wyswietlana etykiete, nie klucz wyszukiwania w wierszu (`row.get(col)`).
_HEADERS_PL: dict[str, str] = {
    # e-commerce
    "week": "tydzień", "week_start": "początek", "days": "dni", "orders": "zamówienia",
    "revenue": "przychód", "profit": "zysk", "margin_pct": "marża", "aov": "śr. wartość",
    "category": "kategoria", "revenue_share_pct": "udział %", "product": "produkt", "units": "sztuki",
    "order_id": "nr zam.", "date": "data", "cost": "koszt", "discount": "rabat",
    "type": "typ", "discount_pct": "rabat", "note": "uwaga",
    # esports (cs2)
    "map": "mapa", "matches": "mecze", "win_rate": "% wygranych", "avg_rating": "śr. rating",
    "avg_adr": "śr. ADR", "kd": "K/D", "played_at": "data", "opponent": "przeciwnik",
    "result": "wynik", "score_team": "rundy (my)", "score_opponent": "rundy (rywal)",
    "kills": "zabójstwa", "deaths": "śmierci", "rating": "rating", "adr": "ADR",
}


def report_title(pipeline: str, language: str = DEFAULT_LANGUAGE) -> str:
    """Tytul raportu dla pipeline'u w danym jezyku (ten sam w naglowku Markdown i w <title> HTML)."""
    by_lang = _TITLES.get(pipeline, {})
    return by_lang.get(language) or by_lang.get("en") or f"Report {pipeline}"


def _headers_for(cols: list[str], language: str) -> list[str] | None:
    """Etykiety naglowkow dla jezyka: PL -> z mapy (klucz bez tlumaczenia zostaje), inny jezyk -> None (= klucze,
    zachowanie EN)."""
    if language == "pl":
        return [_HEADERS_PL.get(c, c) for c in cols]
    return None


@dataclass
class ReportPaths:
    md: Path
    json: Path
    html: Path
    narrative: str
    narrative_source: str


class ReportGenerator:
    def __init__(self, reports_dir: Path, llm_api_key: str | None = None):
        """`llm_api_key` ustawiony tylko przy DAAS_LLM_NARRATIVE=true; None = zawsze szablon, zero ruchu do dostawcy."""
        self.reports_dir = Path(reports_dir)
        self.llm_api_key = llm_api_key
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def write(self, pipeline: str, run_id: str, data_status: DataStatus, metrics: Any,
              source_detail: str, records: int, started_at: datetime,
              client_name: str | None = None, language: str = DEFAULT_LANGUAGE) -> ReportPaths:
        stamp = started_at.strftime("%Y%m%d_%H%M%S")
        base = f"{pipeline}_{stamp}_{run_id[:8]}"
        json_path = self.reports_dir / f"{base}.json"
        md_path = self.reports_dir / f"{base}.md"
        html_path = self.reports_dir / f"{base}.html"

        narrative, narrative_source = build_narrative(pipeline, metrics, client_name, api_key=self.llm_api_key)

        payload = {
            "run_id": run_id,
            "pipeline": pipeline,
            "client_name": client_name,
            "data_status": data_status.value,
            "source_detail": source_detail,
            "records_processed": records,
            "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "narrative": narrative,
            "narrative_source": narrative_source,
            "metrics": sanitize_floats(metrics.model_dump() if hasattr(metrics, "model_dump") else metrics),
        }
        # allow_nan=False: gdyby NaN/inf przeszedl sanityzacje, zapis zawiedzie glosno zamiast wyprodukowac niepoprawny JSON
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str, allow_nan=False),
                             encoding="utf-8")

        if isinstance(metrics, CS2Metrics):
            md = render_cs2_markdown(payload, metrics, language=language)
            title = report_title("cs2_demo", language)
        elif isinstance(metrics, EcommerceMetrics):
            md = render_ecommerce_markdown(payload, metrics, language=language)
            title = report_title("ecommerce_demo", language)
        elif isinstance(metrics, UkscMetrics):
            from app.reporting.uksc_report import render_uksc_markdown
            md = render_uksc_markdown(payload, metrics)
            title = "Raport statusu UKSC — artefakty techniczne stacji"
        else:
            dump = json.dumps(payload, indent=2, default=str, allow_nan=False).replace("```", "'" * 3)
            md = f"# Report {pipeline}\n\n```json\n{dump}\n```\n"
            title = f"Report {pipeline}"
        md_path.write_text(md, encoding="utf-8")

        # w HTML streszczenie idzie do osobnego boxu — usuwamy blockquote z MD, żeby nie dublować
        md_for_html = re.sub(r"^> .*(?:\n>.*)*\n\n", "", md, count=1, flags=re.MULTILINE)
        html_doc = render_html(md_for_html, title=title, data_status=data_status.value, narrative=narrative,
                               narrative_source=narrative_source, client_name=client_name)
        html_path.write_text(html_doc, encoding="utf-8")

        # zawsze aktualny "latest" per pipeline (wygodne dla n8n / dashboardów / linku dla klienta)
        for src, ext in ((json_path, "json"), (md_path, "md"), (html_path, "html")):
            (self.reports_dir / f"{pipeline}_latest.{ext}").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return ReportPaths(md=md_path, json=json_path, html=html_path,
                           narrative=narrative, narrative_source=narrative_source)


# ----------------------------------------------------------------------
def _header(payload: dict[str, Any], title: str) -> str:
    badge = {"LIVE_DATA": "🟢 LIVE_DATA", "MOCK_DATA": "🟡 MOCK_DATA", "BLOCKED_MISSING_SECRET": "🔴 BLOCKED"}
    client = f"| klient | {md_text(payload['client_name'])} |\n" if payload.get("client_name") else ""
    return (
        f"# {title}\n\n"
        f"| pole | wartość |\n|---|---|\n"
        f"{client}"
        f"| run_id | `{payload['run_id']}` |\n"
        f"| pipeline | `{payload['pipeline']}` |\n"
        f"| źródło danych | {badge.get(payload['data_status'], payload['data_status'])} |\n"
        f"| szczegół źródła | {md_text(payload['source_detail'])} |\n"
        f"| rekordów | {payload['records_processed']} |\n"
        f"| wygenerowano | {payload['generated_at']} |\n\n"
    )


def _pln(x: float | int | None) -> str:
    """Format PL: 7 255,95 (spacja tysięcy, przecinek dziesiętny). Patrz `app.reporting.fmt.pln`."""
    return pln(x)


def _delta(value: float | None, unit: str = "%") -> str:
    if value is None:
        return "n/d"
    arrow = "▲" if value > 0 else "▼" if value < 0 else "="
    return f"{arrow} {pp(value) if unit.strip() == 'pp' else pct(value, 1, signed=True)}"


#: Formatery komorek tabel e-commerce: kwoty -> `7 255,95`, procenty -> `99,59 %` (brak wartosci -> `n/d`).
def _fmt_pct1(value: Any) -> str:
    return pct(value, 1)


def _fmt_discount(value: Any) -> str:
    """Rabat w danych zamowien to ulamek 0-1 (0,3 = 30 %)."""
    return pct(None if value is None else 100 * float(value), 0)


_MONEY_COLS: dict[str, Callable[[Any], str]] = {"revenue": pln, "cost": pln, "profit": pln, "aov": pln}


def _table(rows: list[dict[str, Any]], cols: list[str], formats: dict[str, Callable[[Any], str]] | None = None,
           headers: list[str] | None = None) -> str:
    if not rows:
        return "_brak danych_\n"
    fmt = formats or {}
    labels = headers or cols   # `cols` to klucze danych; `headers` (gdy podane) to zlokalizowane etykiety

    def cell(row: dict[str, Any], col: str) -> str:
        value = row.get(col, "")
        return fmt[col](value) if col in fmt else md_text(value)

    # etykiety naglowkow sa kontrolowane przez kod (klucze albo mapa PL), nie pochodza od klienta -> bez escapowania
    head = "| " + " | ".join(labels) + " |\n|" + "---|" * len(cols) + "\n"
    body = "".join("| " + " | ".join(cell(r, c) for c in cols) + " |\n" for r in rows)
    return head + body


def render_cs2_markdown(payload: dict[str, Any], m: CS2Metrics, language: str = DEFAULT_LANGUAGE) -> str:
    verdict = "FORMA WYSOKA" if m.form_score >= 65 else "FORMA STABILNA" if m.form_score >= 45 else "FORMA SŁABA"
    s = _header(payload, report_title("cs2_demo", language))
    s += f"## WNIOSEK\n\n**{verdict}** — form score **{m.form_score}/100**, trend **{m.form_trend}**, "
    s += f"win rate {m.win_rate}% z {m.matches} meczów, K/D {m.kd_ratio}, rating {m.avg_rating}.\n\n"
    if payload.get("narrative"):
        s += f"> {md_text(payload['narrative'])}\n>\n> _{source_label(payload.get('narrative_source'))}_\n\n"
    s += "## Metryki zbiorcze\n\n"
    s += _table([
        {"metryka": "Mecze (W/L/T)", "wartość": f"{m.matches} ({m.wins}/{m.losses}/{m.ties})"},
        {"metryka": "Win rate", "wartość": f"{m.win_rate}%"},
        {"metryka": "K/D", "wartość": f"{m.kd_ratio} ({m.kills}/{m.deaths})"},
        {"metryka": "Asysty", "wartość": m.assists},
        {"metryka": "Avg rating", "wartość": m.avg_rating},
        {"metryka": "Avg ADR", "wartość": m.avg_adr},
        {"metryka": "HS%", "wartość": f"{m.hs_pct}%"},
        {"metryka": "Form score", "wartość": f"{m.form_score}/100 ({m.form_trend})"},
        {"metryka": "Najlepsza / najgorsza mapa", "wartość": f"{md_text(m.best_map)} / {md_text(m.worst_map)}"},
    ], ["metryka", "wartość"])
    map_cols = ["map", "matches", "win_rate", "avg_rating", "avg_adr", "kd"]
    s += "\n## Mapy\n\n" + _table(m.map_breakdown, map_cols, headers=_headers_for(map_cols, language))
    last5_cols = ["played_at", "map", "opponent", "result", "score_team", "score_opponent", "kills", "deaths",
                  "rating", "adr"]
    s += "\n## Ostatnie 5 meczów\n\n" + _table(m.last5, last5_cols, headers=_headers_for(last5_cols, language))
    s += "\n## PRAKTYCZNE WNIOSKI\n\n"
    if m.worst_map and m.best_map and m.worst_map != m.best_map:
        s += (f"- Trenuj **{md_text(m.worst_map)}** (najsłabszy WR) albo banuj ją w veto; "
              f"graj **{md_text(m.best_map)}** gdy masz wybór.\n")
    if m.form_trend == "DOWN":
        s += "- Trend ratingu spada — ogranicz sesje do 2-3 meczów i wróć do DM/aim-trainera przed kolejną serią.\n"
    elif m.form_trend == "UP":
        s += "- Trend rośnie — to moment na granie rankingowych / turniejowych meczów, nie na eksperymenty.\n"
    if m.hs_pct < 45:
        s += f"- HS% {m.hs_pct}% poniżej 45% — 15 min crosshair-placement dziennie przez tydzień.\n"
    s += "- Kolejny run pipeline'u po 5 nowych meczach, żeby form score był porównywalny okno-do-okna.\n"
    return s


def render_ecommerce_markdown(payload: dict[str, Any], m: EcommerceMetrics, language: str = DEFAULT_LANGUAGE) -> str:
    if m.margin_pct is None:
        verdict = "MARŻA NIEOKREŚLONA"
    else:
        verdict = "MARŻA ZDROWA" if m.margin_pct >= 25 else "MARŻA POD PRESJĄ" if m.margin_pct >= 10 else "MARŻA KRYTYCZNA"
    s = _header(payload, report_title("ecommerce_demo", language))
    s += f"## WNIOSEK\n\n**{verdict}** — marża **{pct(m.margin_pct, 2)}**, przychód **{_pln(m.revenue_total)}**, "
    s += f"zysk **{_pln(m.profit_total)}**, {m.loss_orders_count} zamówień stratnych (łącznie {_pln(m.loss_orders_total)}).\n\n"
    if m.wow:
        w = m.wow
        s += (f"**Tydzień {w['week']} vs {w['prev_week']}:** przychód {_delta(w['revenue_delta_pct'])} "
              f"({_pln(w['revenue'])} vs {_pln(w['revenue_prev'])}), zysk {_delta(w['profit_delta_pct'])}, "
              f"marża {_delta(w['margin_delta_pp'], ' pp')} ({pct(w['margin_pct'])} vs {pct(w['margin_prev'])}), "
              f"zamówienia {w['orders']} vs {w['orders_prev']}"
              + (" — ⚠ ostatni tydzień niepełny" if w.get("partial_week") else "") + ".\n\n")
    if payload.get("narrative"):
        s += f"> {md_text(payload['narrative'])}\n>\n> _{source_label(payload.get('narrative_source'))}_\n\n"
    s += "## Metryki zbiorcze\n\n"
    s += _table([
        {"metryka": "Zamówienia (wszystkie)", "wartość": m.orders_total},
        {"metryka": "Completed / refunded / cancelled", "wartość": f"{m.orders_completed} / {m.orders_refunded} / {m.orders_cancelled}"},
        {"metryka": "Przychód", "wartość": _pln(m.revenue_total)},
        {"metryka": "Koszt", "wartość": _pln(m.cost_total)},
        {"metryka": "Zysk", "wartość": _pln(m.profit_total)},
        {"metryka": "Marża", "wartość": pct(m.margin_pct, 2)},
        {"metryka": "Średnia wartość zamówienia", "wartość": "n/d" if m.avg_order_value is None else _pln(m.avg_order_value)},
        {"metryka": "Średni rabat", "wartość": pct(m.avg_discount_pct, 2)},
    ], ["metryka", "wartość"])
    if m.weekly:
        weekly_cols = ["week", "week_start", "days", "orders", "revenue", "profit", "margin_pct", "aov"]
        s += "\n## Tydzień do tygodnia\n\n" + _table(
            m.weekly, weekly_cols, {**_MONEY_COLS, "margin_pct": _fmt_pct1},
            headers=_headers_for(weekly_cols, language))
        if not m.wow:
            s += "\n_Jeden tydzień w danych — porównanie tydzień-do-tygodnia pojawi się od drugiego tygodnia._\n"
    cat_cols = ["category", "orders", "revenue", "profit", "margin_pct", "revenue_share_pct"]
    s += "\n## Top kategorie\n\n" + _table(
        m.top_categories, cat_cols, {**_MONEY_COLS, "margin_pct": _fmt_pct1, "revenue_share_pct": _fmt_pct1},
        headers=_headers_for(cat_cols, language))
    if any(c.get("margin_pct") is None for c in m.top_categories):
        s += "\n_n/d — kategoria bez przychodu (marża nieokreślona)._\n"
    prod_cols = ["product", "orders", "units", "revenue", "profit"]
    s += "\n## Top produkty\n\n" + _table(m.top_products, prod_cols, _MONEY_COLS,
                                          headers=_headers_for(prod_cols, language))
    loss_cols = ["order_id", "date", "product", "category", "revenue", "cost", "profit", "discount"]
    s += "\n## Zamówienia stratne\n\n" + _table(
        m.loss_orders, loss_cols, {**_MONEY_COLS, "discount": _fmt_discount},
        headers=_headers_for(loss_cols, language))
    anomaly_cols = ["order_id", "type", "product", "revenue", "profit", "discount_pct", "note"]
    s += "\n## Anomalie\n\n" + _table(
        m.anomalies, anomaly_cols, {**_MONEY_COLS, "discount_pct": _fmt_pct1},
        headers=_headers_for(anomaly_cols, language))
    s += "\n## PRAKTYCZNE WNIOSKI\n\n"
    if m.wow:
        w = m.wow
        if w["revenue_delta_pct"] is not None and w["revenue_delta_pct"] <= -15 and not w.get("partial_week"):
            s += (f"- Przychód spadł o {pct(abs(w['revenue_delta_pct']))} tydzień do tygodnia ({_pln(w['revenue_prev'])} → {_pln(w['revenue'])}) — "
                  f"sprawdź w tym tygodniu: ruch/reklamy, dostępność top produktów, konkurencję cenową.\n")
        if w["margin_delta_pp"] <= -3:
            s += f"- Marża spadła o {pp(abs(w['margin_delta_pp'])).lstrip('+')} ({pct(w['margin_prev'])} → {pct(w['margin_pct'])}) — przejrzyj rabaty i koszty zakupu z ostatniego tygodnia.\n"
        elif w["margin_delta_pp"] >= 3:
            s += f"- Marża wzrosła o {pp(w['margin_delta_pp']).lstrip('+')} ({pct(w['margin_prev'])} → {pct(w['margin_pct'])}) — utrzymaj politykę rabatową z tego tygodnia.\n"
    if m.loss_orders_count:
        worst = m.loss_orders[0]
        s += f"- Zablokuj rabaty > 20% na **{md_text(worst['product'])}** — każde takie zamówienie kończy się stratą.\n"
        s += f"- Ustaw twardy floor marży 5% w koszyku; {m.loss_orders_count} zamówień by go nie przeszło.\n"
    if m.top_categories:
        top = m.top_categories[0]
        s += f"- **{md_text(top['category'])}** = {pct(top['revenue_share_pct'])} przychodu — koncentracja ryzyka, dywersyfikuj ofertę lub zabezpiecz dostawcę.\n"
    if m.orders_refunded:
        s += f"- {m.orders_refunded} zwrot(y) — sprawdź opis/foto produktu, zwroty to najtańszy do naprawienia wyciek marży.\n"
    s += "- Kolejny run: tygodniowo (poniedziałek 07:00) przez n8n — sekcja „Tydzień do tygodnia” porówna się automatycznie.\n"
    return s
