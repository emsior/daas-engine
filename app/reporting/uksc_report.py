"""Render Markdown raportu 'Dowód UKSC' (HTML powstaje z tego MD przez app.reporting.html).

Struktura celowo pod czytelnika-kierownika (art. 73a) i kontrolera (art. 10):
1. Podsumowanie   2. Kontrola -> artykuł -> status -> dowód   3. Do poświadczenia ręcznie
4. Diff vs poprzedni run   5. Metryka paczki (integralność)
Słownictwo: "dokumentuje", "wykazuje", "wymaga poświadczenia". Nigdy: "certyfikuje", "gwarantuje zgodność".
Nazwy kontroli po polsku: app.reporting.uksc_pl (dane w paczce zostają w ASCII).
"""
from __future__ import annotations

import json
from typing import Any

from app.core.models import UkscMetrics
from app.reporting import uksc_pl

STATUS_LABEL = uksc_pl.STATUS_LABEL


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
    lines.append(f"# Dowód UKSC — raport zgodności technicznej stacji `{m.host}`")
    lines.append("")
    lines.append(f"> {payload.get('narrative', '')}")
    lines.append("")
    lines.append(f"- Klient: **{client}** · Stacja: **{m.host}** · Zebrano (UTC): {m.collected_at_utc:%Y-%m-%d %H:%M}")
    lines.append(f"- Uprawnienia collectora: {'administrator' if m.is_admin_run else 'UŻYTKOWNIK (część kontroli niezebrana)'}")
    lines.append(f"- Run: `{payload.get('run_id', '')[:8]}` · Dane: {payload.get('data_status')} · {src}")
    lines.append("- Podstawa: art. 8 ust. 1 UKSC (Dz.U. 2026 poz. 252, obowiązuje od 03.04.2026). "
                 "Progi referencyjne przy kontrolach: OWU PZU Cyber (UZ/162/2023) — oznaczone jako referencja, nie wymóg ustawowy.")
    lines.append("")
    lines.append("## 1. Podsumowanie")
    lines.append("")
    lines.append("| Kontrole | Automatycznie udokumentowane | PASS | FAIL | WARN | Poświadczenie | Niezebrane |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(f"| {m.checks_total} | {m.passed + m.failed + m.warned} ({m.coverage_pct}%) | {m.passed} | {m.failed} | "
                 f"{m.warned} | {m.manual} | {m.na_no_admin + m.errors} |")
    lines.append("")
    lines.append("## 2. Pokrycie artykułów ustawy")
    lines.append("")
    lines.append("| Odniesienie UKSC | Kontrole | PASS | FAIL | WARN | Poświadczenie | Niezebrane | Werdykt |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for a in m.by_article:
        lines.append(f"| {uksc_pl.ref(a['uksc_ref'])} | {a['controls']} | {a['pass']} | {a['fail']} | {a['warn']} | "
                     f"{a['manual']} | {a['not_collected']} | **{a['verdict']}** |")
    lines.append("")
    lines.append("## 3. Kontrole do naprawy (FAIL / WARN)")
    lines.append("")
    if m.failed_controls:
        lines.append("| ID | Kontrola | Odniesienie | Status | Próg referencyjny | Dowód |")
        lines.append("|---|---|---|---|---|---|")
        for c in m.failed_controls:
            lines.append(f"| {c['control_id']} | {uksc_pl.title(c['control_id'], c['title'])} | {uksc_pl.ref(c['uksc_ref'])} | "
                         f"**{c['status']}** | {c.get('reference_threshold') or '-'} | `{_ev(c.get('evidence') or {})}` |")
    else:
        lines.append("Brak kontroli automatycznych ze statusem FAIL/WARN.")
    lines.append("")
    lines.append("## 4. Do poświadczenia ręcznie / niezebrane")
    lines.append("")
    if m.manual_controls:
        lines.append("| ID | Kontrola | Odniesienie | Status | Co dołączyć |")
        lines.append("|---|---|---|---|---|")
        for c in m.manual_controls:
            default = ("uruchomić collector jako administrator" if c["status"] == "NA_NO_ADMIN"
                       else "dokument / oświadczenie właściciela kontroli")
            what = uksc_pl.hint(c["control_id"], (c.get("evidence") or {}).get("attestation_hint")) or default
            lines.append(f"| {c['control_id']} | {uksc_pl.title(c['control_id'], c['title'])} | {uksc_pl.ref(c['uksc_ref'])} | "
                         f"{STATUS_LABEL.get(c['status'], c['status'])} | {what} |")
    else:
        lines.append("Wszystkie kontrole udokumentowane automatycznie.")
    lines.append("")
    if m.diff_vs_previous:
        d = m.diff_vs_previous
        lines.append("## 5. Zmiany względem poprzedniego runu tej stacji")
        lines.append("")
        lines.append(f"- Poprawione (FAIL/WARN → PASS): {', '.join(d['improved']) or 'brak'}")
        lines.append(f"- Regresje (PASS → FAIL/WARN): {', '.join(d['regressed']) or 'brak'}")
        lines.append(f"- Nowe kontrole: {', '.join(d['new_controls']) or 'brak'} · Usunięte: {', '.join(d['removed_controls']) or 'brak'}")
        lines.append("")
    lines.append("## Metryka paczki dowodowej")
    lines.append("")
    lines.append(f"- `package_sha256`: `{m.package_sha256 or 'brak'}`")
    lines.append(f"- Integralność paczki przy imporcie: **{m.integrity or 'nieznana'}**")
    lines.append("- Weryfikacja: sha256 z posortowanej konkatenacji `evidence_hash` wszystkich kontroli "
                 "(każdy `evidence_hash` = sha256 kanonicznego JSON dowodu) musi dać powyższy `package_sha256`.")
    lines.append("")
    lines.append("Raport jest zapisem operacyjnym w rozumieniu art. 10 UKSC: stan techniczny stacji w chwili zebrania, "
                 "z hashem paczki weryfikowalnym niezależnie. Nie stanowi oceny prawnej ani potwierdzenia wdrożenia SZBI.")
    lines.append("")
    return "\n".join(lines)
