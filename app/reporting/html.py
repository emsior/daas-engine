"""Renderer HTML: samodzielny plik (inline CSS), z Markdown raportu. Otwiera się w każdej przeglądarce,
da się wysłać mailem jako załącznik, wydrukować do PDF (Ctrl+P)."""
from __future__ import annotations

import html
from datetime import datetime

import markdown

CSS = """
:root { --bg:#f7f7f5; --card:#fff; --ink:#1c1c1a; --muted:#6b6b66; --line:#e6e6e2; --accent:#0f6b52; --warn:#b5541b; --bad:#a32d2d; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
.wrap { max-width:980px; margin:32px auto; padding:0 20px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:28px 32px; }
h1 { font-size:26px; margin:0 0 6px; letter-spacing:-.01em; }
h2 { font-size:17px; margin:30px 0 10px; padding-bottom:6px; border-bottom:1px solid var(--line); text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
h2:first-of-type { margin-top:18px; }
.meta { color:var(--muted); font-size:13px; margin-bottom:8px; }
.badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; font-weight:600; letter-spacing:.03em; }
.b-live { background:#dff3ea; color:var(--accent); } .b-mock { background:#fff1dc; color:var(--warn); } .b-blocked { background:#fde3e3; color:var(--bad); }
.summary { background:#f2f7f5; border-left:4px solid var(--accent); padding:14px 18px; border-radius:6px; margin:14px 0 6px; }
.summary .src { color:var(--muted); font-size:12px; margin-top:8px; }
table { border-collapse:collapse; width:100%; margin:8px 0 4px; font-size:14px; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
tr:last-child td { border-bottom:none; }
td:nth-child(n+3), th:nth-child(n+3) { font-variant-numeric:tabular-nums; }
code { background:#f1f1ee; padding:1px 5px; border-radius:4px; font-size:13px; }
ul { padding-left:20px; } li { margin:4px 0; }
.foot { color:var(--muted); font-size:12px; margin-top:26px; text-align:center; }
@media print { body{background:#fff} .wrap{margin:0} .card{border:none;padding:0} }
"""


def render_html(md_text: str, *, title: str, data_status: str, narrative: str | None,
                narrative_source: str | None, client_name: str | None = None) -> str:
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    badge_cls = {"LIVE_DATA": "b-live", "MOCK_DATA": "b-mock"}.get(data_status, "b-blocked")
    summary_html = ""
    if narrative:
        summary_html = (
            f'<div class="summary"><strong>Streszczenie</strong><br>{html.escape(narrative)}'
            f'<div class="src">źródło streszczenia: {narrative_source or "TEMPLATE"} · liczby pochodzą wyłącznie z policzonych metryk</div></div>'
        )
    head = (
        f'<div class="meta">{html.escape(client_name) + " · " if client_name else ""}'
        f'<span class="badge {badge_cls}">{html.escape(data_status)}</span> · '
        f'wygenerowano {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC</div>'
    )
    return (
        "<!doctype html><html lang=\"pl\"><head><meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{html.escape(title)}</title>"
        f"<style>{CSS}</style></head><body><div class=\"wrap\"><div class=\"card\">{head}{summary_html}{body}</div>"
        "<div class=\"foot\">DaaS Engine · raport wygenerowany automatycznie · liczby z DuckDB, narracja opisowa</div>"
        "</div></body></html>"
    )
