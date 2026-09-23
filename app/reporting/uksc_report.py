"""Render Markdown raportu 'Dowod UKSC' (HTML powstaje z tego MD przez app.reporting.html).

Struktura celowo pod czytelnika-kierownika (art. 73a) i kontrolera (art. 10):
1. Podsumowanie   2. Kontrola -> artykul -> status -> dowod   3. Do poswiadczenia recznie
4. Diff vs poprzedni run   5. Metryka paczki (integralnosc)
Slownictwo: "dokumentuje", "wykazuje", "wymaga poswiadczenia". Nigdy: "certyfikuje", "gwarantuje zgodnosc".
"""
from __future__ import annotations

import json
from typing import Any

from app.core.models import UkscMetrics

STATUS_LABEL = {
    "PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "MANUAL": "poswiadczenie",
    "NA_NO_ADMIN": "brak admina", "ERROR": "blad zbierania",
}


def _ev(e: dict[str, Any], limit: int = 160) -> str:
    if not e:
        return "-"
    s = json.dumps(e, ensure_ascii=False, sort_keys=True, default=str)
    s = s.replace("|", "\\|")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def render_uksc_markdown(payload: dict[str, Any], m: UkscMetrics) -> str:
    client = payload.get("client_name") or "-"
    src = payload.get("source_detail") or ""
    lines: list[str] = []
    lines.append(f"# Dowod UKSC - raport zgodnosci technicznej stacji `{m.host}`")
    lines.append("")
    lines.append(f"> {payload.get('narrative', '')}")
    lines.append("")
    lines.append(f"- Klient: **{client}** · Stacja: **{m.host}** · Zebrano (UTC): {m.collected_at_utc:%Y-%m-%d %H:%M}")
    lines.append(f"- Uprawnienia collectora: {'administrator' if m.is_admin_run else 'UZYTKOWNIK (czesc kontroli niezebrana)'}")
    lines.append(f"- Run: `{payload.get('run_id', '')[:8]}` · Dane: {payload.get('data_status')} · {src}")
    lines.append("- Podstawa: art. 8 ust. 1 UKSC (Dz.U. 2026 poz. 252, w zycie 03.04.2026). "
                 "Progi referencyjne przy kontrolach: OWU PZU Cyber (UZ/162/2023) - oznaczone jako referencja, nie wymog ustawowy.")
    lines.append("")
    lines.append("## 1. Podsumowanie")
    lines.append("")
    lines.append("| Kontrole | Automatycznie udokumentowane | PASS | FAIL | WARN | Poswiadczenie | Niezebrane |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(f"| {m.checks_total} | {m.passed + m.failed + m.warned} ({m.coverage_pct}%) | {m.passed} | {m.failed} | "
                 f"{m.warned} | {m.manual} | {m.na_no_admin + m.errors} |")
    lines.append("")
    lines.append("## 2. Pokrycie artykulow ustawy")
    lines.append("")
    lines.append("| Odniesienie UKSC | Kontrole | PASS | FAIL | WARN | Poswiadczenie | Niezebrane | Werdykt |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for a in m.by_article:
        lines.append(f"| {a['uksc_ref']} | {a['controls']} | {a['pass']} | {a['fail']} | {a['warn']} | "
                     f"{a['manual']} | {a['not_collected']} | **{a['verdict']}** |")
    lines.append("")
    lines.append("## 3. Kontrole do naprawy (FAIL / WARN)")
    lines.append("")
    if m.failed_controls:
        lines.append("| ID | Kontrola | Odniesienie | Status | Prog referencyjny | Dowod |")
        lines.append("|---|---|---|---|---|---|")
        for c in m.failed_controls:
            lines.append(f"| {c['control_id']} | {c['title']} | {c['uksc_ref']} | **{c['status']}** | "
                         f"{c.get('reference_threshold') or '-'} | `{_ev(c.get('evidence') or {})}` |")
    else:
        lines.append("Brak kontroli automatycznych ze statusem FAIL/WARN.")
    lines.append("")
    lines.append("## 4. Do poswiadczenia recznie / niezebrane")
    lines.append("")
    if m.manual_controls:
        lines.append("| ID | Kontrola | Odniesienie | Status | Co dolaczyc |")
        lines.append("|---|---|---|---|---|")
        for c in m.manual_controls:
            what = (c.get("evidence") or {}).get("attestation_hint") or (
                "uruchomic collector jako administrator" if c["status"] == "NA_NO_ADMIN" else "dokument / oswiadczenie wlasciciela kontroli")
            lines.append(f"| {c['control_id']} | {c['title']} | {c['uksc_ref']} | {STATUS_LABEL.get(c['status'], c['status'])} | {what} |")
    else:
        lines.append("Wszystkie kontrole udokumentowane automatycznie.")
    lines.append("")
    if m.diff_vs_previous:
        d = m.diff_vs_previous
        lines.append("## 5. Zmiany vs poprzedni run tej stacji")
        lines.append("")
        lines.append(f"- Poprawione (FAIL/WARN -> PASS): {', '.join(d['improved']) or 'brak'}")
        lines.append(f"- Regresje (PASS -> FAIL/WARN): {', '.join(d['regressed']) or 'brak'}")
        lines.append(f"- Nowe kontrole: {', '.join(d['new_controls']) or 'brak'} · Usuniete: {', '.join(d['removed_controls']) or 'brak'}")
        lines.append("")
    lines.append("## Metryka paczki dowodowej")
    lines.append("")
    lines.append(f"- `package_sha256`: `{m.package_sha256 or 'brak'}`")
    lines.append(f"- Integralnosc paczki przy imporcie: **{m.integrity or 'nieznana'}**")
    lines.append("- Weryfikacja: sha256 z posortowanej konkatenacji `evidence_hash` wszystkich kontroli "
                 "(kazdy `evidence_hash` = sha256 kanonicznego JSON dowodu) musi dac powyzszy `package_sha256`.")
    lines.append("")
    lines.append("Raport jest zapisem operacyjnym w rozumieniu art. 10 UKSC: stan techniczny stacji w chwili zebrania, "
                 "z hashem paczki weryfikowalnym niezaleznie. Nie stanowi oceny prawnej ani potwierdzenia wdrozenia SZBI.")
    lines.append("")
    return "\n".join(lines)
