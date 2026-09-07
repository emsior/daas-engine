"""Generator raportów JSON + Markdown do runtime/reports/."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from dataclasses import dataclass

from app.core.models import CS2Metrics, DataStatus, EcommerceMetrics
from app.reporting.html import render_html
from app.reporting.narrative import build_narrative


@dataclass
class ReportPaths:
    md: Path
    json: Path
    html: Path
    narrative: str
    narrative_source: str


class ReportGenerator:
    def __init__(self, reports_dir: Path):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def write(self, pipeline: str, run_id: str, data_status: DataStatus, metrics: Any,
              source_detail: str, records: int, started_at: datetime,
              client_name: str | None = None) -> ReportPaths:
        stamp = started_at.strftime("%Y%m%d_%H%M%S")
        base = f"{pipeline}_{stamp}_{run_id[:8]}"
        json_path = self.reports_dir / f"{base}.json"
        md_path = self.reports_dir / f"{base}.md"
        html_path = self.reports_dir / f"{base}.html"

        narrative, narrative_source = build_narrative(pipeline, metrics, client_name)

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
            "metrics": metrics.model_dump() if hasattr(metrics, "model_dump") else metrics,
        }
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        if isinstance(metrics, CS2Metrics):
            md = render_cs2_markdown(payload, metrics)
            title = "CS2 Performance Report"
        elif isinstance(metrics, EcommerceMetrics):
            md = render_ecommerce_markdown(payload, metrics)
            title = "E-Commerce Sales Report"
        else:
            md = f"# Report {pipeline}\n\n```json\n{json.dumps(payload, indent=2, default=str)}\n```\n"
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
    client = f"| klient | {payload['client_name']} |\n" if payload.get("client_name") else ""
    return (
        f"# {title}\n\n"
        f"| pole | wartość |\n|---|---|\n"
        f"{client}"
        f"| run_id | `{payload['run_id']}` |\n"
        f"| pipeline | `{payload['pipeline']}` |\n"
        f"| źródło danych | {badge.get(payload['data_status'], payload['data_status'])} |\n"
        f"| szczegół źródła | {payload['source_detail']} |\n"
        f"| rekordów | {payload['records_processed']} |\n"
        f"| wygenerowano | {payload['generated_at']} |\n\n"
    )


def _pln(x: float | int | None) -> str:
    """Format PL: 7 255,95 (spacja tysięcy, przecinek dziesiętny)."""
    if x is None:
        return "—"
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def _delta(pct: float | None, unit: str = "%") -> str:
    if pct is None:
        return "n/d"
    arrow = "▲" if pct > 0 else "▼" if pct < 0 else "="
    return f"{arrow} {pct:+.1f}{unit}"


def _table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "_brak danych_\n"
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = "".join("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |\n" for r in rows)
    return head + body


def render_cs2_markdown(payload: dict[str, Any], m: CS2Metrics) -> str:
    verdict = "FORMA WYSOKA" if m.form_score >= 65 else "FORMA STABILNA" if m.form_score >= 45 else "FORMA SŁABA"
    s = _header(payload, "CS2 Performance Report")
    s += f"## WNIOSEK\n\n**{verdict}** — form score **{m.form_score}/100**, trend **{m.form_trend}**, "
    s += f"win rate {m.win_rate}% z {m.matches} meczów, K/D {m.kd_ratio}, rating {m.avg_rating}.\n\n"
    if payload.get("narrative"):
        s += f"> {payload['narrative']}\n>\n> _streszczenie: {payload.get('narrative_source', 'TEMPLATE')}_\n\n"
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
        {"metryka": "Najlepsza / najgorsza mapa", "wartość": f"{m.best_map} / {m.worst_map}"},
    ], ["metryka", "wartość"])
    s += "\n## Mapy\n\n" + _table(m.map_breakdown, ["map", "matches", "win_rate", "avg_rating", "avg_adr", "kd"])
    s += "\n## Ostatnie 5 meczów\n\n" + _table(
        m.last5, ["played_at", "map", "opponent", "result", "score_team", "score_opponent", "kills", "deaths", "rating", "adr"]
    )
    s += "\n## PRAKTYCZNE WNIOSKI\n\n"
    if m.worst_map and m.best_map and m.worst_map != m.best_map:
        s += f"- Trenuj **{m.worst_map}** (najsłabszy WR) albo banuj ją w veto; graj **{m.best_map}** gdy masz wybór.\n"
    if m.form_trend == "DOWN":
        s += "- Trend ratingu spada — ogranicz sesje do 2-3 meczów i wróć do DM/aim-trainera przed kolejną serią.\n"
    elif m.form_trend == "UP":
        s += "- Trend rośnie — to moment na granie rankingowych / turniejowych meczów, nie na eksperymenty.\n"
    if m.hs_pct < 45:
        s += f"- HS% {m.hs_pct}% poniżej 45% — 15 min crosshair-placement dziennie przez tydzień.\n"
    s += "- Kolejny run pipeline'u po 5 nowych meczach, żeby form score był porównywalny okno-do-okna.\n"
    return s


def render_ecommerce_markdown(payload: dict[str, Any], m: EcommerceMetrics) -> str:
    verdict = "MARŻA ZDROWA" if m.margin_pct >= 25 else "MARŻA POD PRESJĄ" if m.margin_pct >= 10 else "MARŻA KRYTYCZNA"
    s = _header(payload, "E-Commerce B2B Sales Report")
    s += f"## WNIOSEK\n\n**{verdict}** — marża **{m.margin_pct}%**, przychód **{_pln(m.revenue_total)}**, "
    s += f"zysk **{_pln(m.profit_total)}**, {m.loss_orders_count} zamówień stratnych (łącznie {_pln(m.loss_orders_total)}).\n\n"
    if m.wow:
        w = m.wow
        s += (f"**Tydzień {w['week']} vs {w['prev_week']}:** przychód {_delta(w['revenue_delta_pct'])} "
              f"({_pln(w['revenue'])} vs {_pln(w['revenue_prev'])}), zysk {_delta(w['profit_delta_pct'])}, "
              f"marża {_delta(w['margin_delta_pp'], ' pp')} ({w['margin_pct']}% vs {w['margin_prev']}%), "
              f"zamówienia {w['orders']} vs {w['orders_prev']}"
              + (" — ⚠ ostatni tydzień niepełny" if w.get("partial_week") else "") + ".\n\n")
    if payload.get("narrative"):
        s += f"> {payload['narrative']}\n>\n> _streszczenie: {payload.get('narrative_source', 'TEMPLATE')}_\n\n"
    s += "## Metryki zbiorcze\n\n"
    s += _table([
        {"metryka": "Zamówienia (wszystkie)", "wartość": m.orders_total},
        {"metryka": "Completed / refunded / cancelled", "wartość": f"{m.orders_completed} / {m.orders_refunded} / {m.orders_cancelled}"},
        {"metryka": "Przychód", "wartość": _pln(m.revenue_total)},
        {"metryka": "Koszt", "wartość": _pln(m.cost_total)},
        {"metryka": "Zysk", "wartość": _pln(m.profit_total)},
        {"metryka": "Marża %", "wartość": f"{m.margin_pct}%"},
        {"metryka": "Średnia wartość zamówienia", "wartość": _pln(m.avg_order_value)},
        {"metryka": "Średni rabat", "wartość": f"{m.avg_discount_pct}%"},
    ], ["metryka", "wartość"])
    if m.weekly:
        s += "\n## Tydzień do tygodnia\n\n" + _table(m.weekly, ["week", "week_start", "days", "orders", "revenue", "profit", "margin_pct", "aov"])
        if not m.wow:
            s += "\n_Jeden tydzień w danych — porównanie tydzień-do-tygodnia pojawi się od drugiego tygodnia._\n"
    s += "\n## Top kategorie\n\n" + _table(m.top_categories, ["category", "orders", "revenue", "profit", "margin_pct", "revenue_share_pct"])
    s += "\n## Top produkty\n\n" + _table(m.top_products, ["product", "orders", "units", "revenue", "profit"])
    s += "\n## Zamówienia stratne\n\n" + _table(m.loss_orders, ["order_id", "date", "product", "category", "revenue", "cost", "profit", "discount"])
    s += "\n## Anomalie\n\n" + _table(m.anomalies, ["order_id", "type", "product", "revenue", "profit", "discount_pct", "note"])
    s += "\n## PRAKTYCZNE WNIOSKI\n\n"
    if m.wow:
        w = m.wow
        if w["revenue_delta_pct"] is not None and w["revenue_delta_pct"] <= -15 and not w.get("partial_week"):
            s += (f"- Przychód spadł o {abs(w['revenue_delta_pct'])}% tydzień do tygodnia ({_pln(w['revenue_prev'])} → {_pln(w['revenue'])}) — "
                  f"sprawdź w tym tygodniu: ruch/reklamy, dostępność top produktów, konkurencję cenową.\n")
        if w["margin_delta_pp"] <= -3:
            s += f"- Marża spadła o {abs(w['margin_delta_pp'])} pp ({w['margin_prev']}% → {w['margin_pct']}%) — przejrzyj rabaty i koszty zakupu z ostatniego tygodnia.\n"
        elif w["margin_delta_pp"] >= 3:
            s += f"- Marża wzrosła o {w['margin_delta_pp']} pp ({w['margin_prev']}% → {w['margin_pct']}%) — utrzymaj politykę rabatową z tego tygodnia.\n"
    if m.loss_orders_count:
        worst = m.loss_orders[0]
        s += f"- Zablokuj rabaty > 20% na **{worst['product']}** — każde takie zamówienie kończy się stratą.\n"
        s += f"- Ustaw twardy floor marży 5% w koszyku; {m.loss_orders_count} zamówień by go nie przeszło.\n"
    if m.top_categories:
        top = m.top_categories[0]
        s += f"- **{top['category']}** = {top['revenue_share_pct']}% przychodu — koncentracja ryzyka, dywersyfikuj ofertę lub zabezpiecz dostawcę.\n"
    if m.orders_refunded:
        s += f"- {m.orders_refunded} zwrot(y) — sprawdź opis/foto produktu, zwroty to najtańszy do naprawienia wyciek marży.\n"
    s += "- Kolejny run: tygodniowo (poniedziałek 07:00) przez n8n — sekcja „Tydzień do tygodnia” porówna się automatycznie.\n"
    return s
