"""Streszczenie wykonawcze (executive summary) raportu.

Zasada: LLM NIGDY nie liczy. Dostaje gotowe metryki z DuckDB/pandas i tylko je opisuje.

LLM jest OPT-IN: wywolanie zewnetrznego API nastepuje wylacznie, gdy wywolujacy poda `api_key`,
a runner robi to tylko przy DAAS_LLM_NARRATIVE=true (`Settings.llm_api_key`). Sam ANTHROPIC_API_KEY
w srodowisku niczego nie wysyla. Do dostawcy trafiaja wylacznie zagregowane metryki — bez order_id,
nazw produktow, nazwy klienta i nazwy hosta. Brak klucza albo blad sieci -> deterministyczny szablon.

narrative_source: "LLM" | "TEMPLATE"
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from app.core.models import CS2Metrics, EcommerceMetrics, UkscMetrics
from app.reporting.fmt import pct, pln

log = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = os.getenv("NARRATIVE_MODEL", "claude-sonnet-4-5")

SYSTEM_PROMPT = (
    "Jesteś analitykiem biznesowym. Dostajesz JSON z policzonymi metrykami. "
    "Napisz streszczenie wykonawcze po polsku: 3-5 zdań, rzeczowo, bez marketingu, bez nagłówków. "
    "ZASADA BEZWZGLĘDNA: każda liczba w tekście musi pochodzić wprost z JSON — nie licz nic sam, "
    "nie zaokrąglaj inaczej niż w danych, nie wymyślaj wartości. Jeśli czegoś nie ma w JSON, nie wspominaj o tym. "
    "Zakończ jednym zdaniem z najważniejszą rekomendacją."
)


#: Adnotacja w raporcie, gdy streszczenie pochodzi od modelu.
LLM_ANNOTATION = "streszczenie wygenerowane przez model"

#: Klucze, ktore nigdy nie opuszczaja serwera w payloadzie do LLM (identyfikatory i dane osobowe/handlowe).
_NEVER_SEND = frozenset({"order_id", "product", "client_name", "host", "package_sha256"})


def source_label(source: str | None) -> str:
    """Etykieta zrodla streszczenia do raportu (Markdown/HTML)."""
    if source == "LLM":
        return f"{LLM_ANNOTATION} (LLM)"
    return "streszczenie: TEMPLATE"


def build_narrative(pipeline: str, metrics: Any, client_name: str | None = None, *,
                    api_key: str | None = None) -> tuple[str, str]:
    """Zwraca (tekst, źródło) gdzie źródło ∈ {"LLM", "TEMPLATE"}.

    `api_key=None` (domyslnie) oznacza szablon i brak jakiegokolwiek ruchu sieciowego.
    """
    if api_key:
        try:
            return _llm_narrative(api_key, pipeline, metrics), "LLM"
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM narrative failed (%s: %s) -> template", type(exc).__name__, exc)
    return template_narrative(pipeline, metrics, client_name), "TEMPLATE"


# ----------------------------------------------------------------------
def _strip(node: Any) -> Any:
    """Rekurencyjnie usuwa klucze z `_NEVER_SEND`."""
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in _NEVER_SEND}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def _compact(metrics: Any) -> dict[str, Any]:
    d = metrics.model_dump() if hasattr(metrics, "model_dump") else dict(metrics)
    out = {k: v for k, v in d.items() if not isinstance(v, (list, dict))}
    # kilka list w skrócie, żeby LLM miał kontekst bez zalewu
    for key in ("top_categories", "loss_orders", "map_breakdown"):
        if isinstance(d.get(key), list):
            out[key] = d[key][:3]
    if isinstance(d.get("wow"), dict):
        out["week_over_week"] = d["wow"]
    return _strip(out)


def _llm_narrative(api_key: str, pipeline: str, metrics: Any) -> str:
    payload = {
        "model": DEFAULT_MODEL,
        "max_tokens": 400,
        "system": SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": f"pipeline: {pipeline}\n\nJSON:\n"
                       + json.dumps(_compact(metrics), ensure_ascii=False, default=str),
        }],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    r = httpx.post(ANTHROPIC_URL, headers=headers, json=payload, timeout=30.0)
    r.raise_for_status()
    text = "".join(block.get("text", "") for block in r.json().get("content", []) if block.get("type") == "text")
    if not text.strip():
        raise ValueError("empty LLM response")
    return text.strip()


# ----------------------------------------------------------------------
def _pln(x: float) -> str:
    return pln(x)


def template_narrative(pipeline: str, metrics: Any, client_name: str | None = None) -> str:
    who = f" dla {client_name}" if client_name else ""
    if isinstance(metrics, CS2Metrics):
        m = metrics
        if m.matches == 0:
            return "Brak meczów w analizowanym oknie — streszczenie niedostępne."
        trend = {"UP": "rośnie", "DOWN": "spada", "FLAT": "jest stabilny"}[m.form_trend]
        s = (f"W analizowanym oknie rozegrano {m.matches} meczów (bilans {m.wins}-{m.losses}-{m.ties}, "
             f"win rate {m.win_rate}%). K/D wyniosło {m.kd_ratio} przy średnim ratingu {m.avg_rating} i ADR {m.avg_adr}; "
             f"HS% na poziomie {m.hs_pct}%. Form score to {m.form_score}/100, a trend ratingu {trend}.")
        if m.best_map and m.worst_map and m.best_map != m.worst_map:
            s += f" Najlepsza mapa to {m.best_map}, najsłabsza {m.worst_map}."
        s += (f" Rekomendacja: {'utrzymać obecny rytm gry' if m.form_trend != 'DOWN' else 'skrócić sesje i wrócić do treningu aimu'}"
              f"{f' oraz banować {m.worst_map} w veto' if m.worst_map and m.worst_map != m.best_map else ''}.")
        return s

    if isinstance(metrics, EcommerceMetrics):
        m = metrics
        if m.orders_total == 0:
            return "Brak zamówień w analizowanym pliku — streszczenie niedostępne."
        s = (f"Raport{who} obejmuje {m.orders_total} zamówień ({m.orders_completed} zrealizowanych, "
             f"{m.orders_refunded} zwrotów, {m.orders_cancelled} anulowanych). Przychód wyniósł {_pln(m.revenue_total)} "
             f"przy koszcie {_pln(m.cost_total)}, co daje zysk {_pln(m.profit_total)} i marżę {pct(m.margin_pct, 2)}. "
             f"Średnia wartość zamówienia to {_pln(m.avg_order_value)}, średni rabat {pct(m.avg_discount_pct, 2)}.")
        if m.loss_orders_count:
            s += (f" Wykryto {m.loss_orders_count} zamówień stratnych o łącznym wyniku {_pln(m.loss_orders_total)} — "
                  f"to pierwszy obszar do naprawy.")
        if m.wow:
            w = m.wow
            dr = w["revenue_delta_pct"]
            kier = "wzrósł" if (dr or 0) > 0 else "spadł" if (dr or 0) < 0 else "nie zmienił się"
            s += (f" Tydzień {w['week']} vs {w['prev_week']}: przychód {kier}"
                  f"{f' o {pct(abs(dr))}' if dr else ''}, marża {pct(w['margin_prev'])} → {pct(w['margin_pct'])}"
                  f"{' (ostatni tydzień niepełny)' if w.get('partial_week') else ''}.")
        if m.top_categories:
            top = m.top_categories[0]
            s += (f" Największą kategorią jest {top['category']} ({pct(top['revenue_share_pct'])} przychodu, "
                  f"marża {pct(top['margin_pct'])}).")
        rec = ("ograniczyć rabaty na produktach, które generują straty" if m.loss_orders_count
               else "utrzymać politykę cenową i monitorować marżę tydzień do tygodnia")
        s += f" Rekomendacja: {rec}."
        return s

    if isinstance(metrics, UkscMetrics):
        m = metrics
        s = (f"Stacja {m.host}: {m.checks_total} kontroli technicznych, z czego {m.passed + m.failed + m.warned} "
             f"udokumentowanych automatycznie ({m.coverage_pct}% pokrycia), {m.manual} wymaga poświadczenia dokumentem")
        if m.na_no_admin:
            s += f", {m.na_no_admin} nie zebrano (collector bez uprawnień administratora)"
        s += f". Wynik kontroli automatycznych: {m.passed} PASS, {m.failed} FAIL, {m.warned} WARN ({m.pass_pct}% PASS)."
        if m.failed_controls:
            ids = ", ".join(str(c["control_id"]) for c in m.failed_controls[:5])
            s += f" Do naprawy w pierwszej kolejności: {ids}."
        if m.diff_vs_previous:
            d = m.diff_vs_previous
            s += f" Względem poprzedniego runu: poprawione {len(d['improved'])}, regresje {len(d['regressed'])}."
        s += " Raport dokumentuje stan techniczny stacji (zapis wg art. 10 UKSC); nie zastępuje oceny prawnej SZBI."
        return s

    return "Streszczenie niedostępne dla tego typu metryk."
