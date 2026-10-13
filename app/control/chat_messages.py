"""Wiadomosc o zakonczonym runie na czacie zespolu: Slack (Incoming Webhook), Microsoft Teams (Workflows) albo
dowolny odbiorca JSON (`generic`).

Wejscie (`RunMessage`) to wylacznie metadane runu: nazwa organizacji po `sanitize_org_name`, pipeline, status (run
`PARTIAL` = czesciowy), okres danych, SHA-256 raportu HTML, kod bledu, czas zakonczenia i link do widoku runu
w kokpicie (bez tokenu). Zadnej tresci raportu ani danych klienta. Teksty Slack/Teams sa po polsku; `generic` ma pola
o nazwach angielskich i nie jest podpisywany: adres webhooka jest sekretem (podpisane zdarzenia daje `/webhooks`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.control.executor import RunFinished
from app.control.mailer import _cockpit_origin, sanitize_org_name
from app.control.webhooks import EVENT_RUN_FINISHED, run_finished_fields
from app.core.config import Settings

#: Rodzaje odbiorcow (`OrgSchedule.notify_webhook_kind`); pierwszy jest domyslny.
CHAT_KINDS: tuple[str, ...] = ("slack", "teams", "generic")
DEFAULT_KIND = CHAT_KINDS[0]
OPEN_RUN = "Otwórz run"
TEAMS_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"


@dataclass(frozen=True)
class RunMessage:
    """Fakty o zakonczonym runie do wiadomosci na czacie (bez tresci raportu i danych klienta)."""

    event: RunFinished
    organization: str  # nazwa po `sanitize_org_name`
    period_from: str | None
    period_to: str | None
    report_sha256: str | None
    error_code: str | None
    link: str

    @property
    def succeeded(self) -> bool:
        return self.event.status == "succeeded"

    @property
    def partial(self) -> bool:
        """Run udany, ale niepelny (`run_status=PARTIAL`); porazka ma pierwszenstwo."""
        return self.succeeded and self.event.run_status == "PARTIAL"


def run_message(settings: Settings, event: RunFinished, *, org_name: str, period: tuple[str | None, str | None],
                report_sha256: str | None, error_code: str | None) -> RunMessage:
    """`RunMessage` z danych runu: nazwa organizacji oczyszczona, link do `/runs/{run_id}` w kokpicie."""
    return RunMessage(event=event, organization=sanitize_org_name(org_name), period_from=period[0],
                      period_to=period[1], report_sha256=report_sha256, error_code=error_code,
                      link=f"{_cockpit_origin(settings)}/runs/{event.run_id}")


def headline(msg: RunMessage) -> str:
    if not msg.succeeded:
        return "Przetwarzanie raportu nie powiodło się"
    return "Raport jest gotowy (częściowy)" if msg.partial else "Raport jest gotowy"


def status_label(msg: RunMessage) -> str:
    if not msg.succeeded:
        return "błąd"
    return "zakończony częściowo" if msg.partial else "zakończony"


def _finished_label(iso: str) -> str:
    """`2026-10-12T08:00:00+00:00` -> `2026-10-12 08:00 UTC` (wartosc nieczytelna zostaje bez zmian)."""
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def facts(msg: RunMessage) -> list[tuple[str, str]]:
    """Pary (etykieta, wartosc) w kolejnosci wyswietlania; te same co w mailu po runie plus czas zakonczenia."""
    rows = [("Organizacja", msg.organization), ("Pipeline", msg.event.pipeline), ("Status", status_label(msg))]
    if msg.period_from or msg.period_to:
        rows.append(("Okres danych", f"{msg.period_from or '?'} – {msg.period_to or '?'}"))
    if msg.report_sha256:
        rows.append(("SHA-256 raportu (HTML)", msg.report_sha256))
    if msg.error_code and not msg.succeeded:
        rows.append(("Kod błędu", msg.error_code))
    rows.append(("Zakończono", _finished_label(msg.event.finished_at_iso)))
    return rows


def _slack_escape(text: str) -> str:
    """Slack mrkdwn: `&`, `<`, `>` jako encje (inaczej `<...>` bylby linkiem albo wzmianka)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_payload(msg: RunMessage) -> dict[str, Any]:
    """Slack Incoming Webhook: `text` (powiadomienie, jedna linia) + bloki: sekcja z faktami i przycisk do runu."""
    lines = [f"*{_slack_escape(headline(msg))}*"]
    lines += [f"*{label}:* {_slack_escape(value)}" for label, value in facts(msg)]
    fallback = f"{headline(msg)}: {msg.event.pipeline}, {msg.organization} ({status_label(msg)})"
    return {
        "text": _slack_escape(fallback),
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": OPEN_RUN}, "url": msg.link}]},
        ],
    }


def teams_payload(msg: RunMessage) -> dict[str, Any]:
    """Microsoft Teams (Workflows, wyzwalacz "When a Teams webhook request is received"): wiadomosc z karta
    Adaptive Card 1.4 - naglowek, FactSet i `Action.OpenUrl` do runu."""
    color = "Attention" if not msg.succeeded else ("Warning" if msg.partial else "Good")
    card = {
        "$schema": TEAMS_CARD_SCHEMA,
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": headline(msg), "weight": "Bolder", "size": "Medium", "color": color,
             "wrap": True},
            {"type": "FactSet", "facts": [{"title": label, "value": value} for label, value in facts(msg)]},
        ],
        "actions": [{"type": "Action.OpenUrl", "title": OPEN_RUN, "url": msg.link}],
    }
    return {"type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


def generic_payload(msg: RunMessage) -> dict[str, Any]:
    """Dowolny odbiorca JSON: pola `run.finished` (jak webhooki HMAC) + organizacja, okres, hash, kod bledu i link."""
    return {"event": EVENT_RUN_FINISHED, **run_finished_fields(msg.event), "organization": msg.organization,
            "period_from": msg.period_from, "period_to": msg.period_to, "report_sha256": msg.report_sha256,
            "error_code": msg.error_code, "link": msg.link}


_BUILDERS = {"slack": slack_payload, "teams": teams_payload, "generic": generic_payload}


def build_payload(kind: str, msg: RunMessage) -> dict[str, Any]:
    """Payload wg rodzaju odbiorcy (`CHAT_KINDS`); nieznany rodzaj -> `ValueError`."""
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise ValueError("unknown chat webhook kind")
    return builder(msg)


def encode(payload: dict[str, Any]) -> bytes:
    """Cialo zadania: JSON w UTF-8 (posortowane klucze, bez spacji)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
