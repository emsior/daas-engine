"""Wysylka e-maili: adapter `console` (dev/e2e) i `smtp` (stdlib `smtplib`, STARTTLS).

`console` zapisuje wiadomosc do `runtime/outbox/*.json` (plik 0600) i loguje link — przeznaczony wylacznie
do rozwoju i testow e2e. Produkcja: `DAAS_EMAIL_BACKEND=smtp`. Wysylka jest blokujaca; wolac przez
`run_in_threadpool`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import smtplib
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from app.core.config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutgoingMail:
    to: str
    subject: str
    body: str
    link: str | None = None


class Mailer(Protocol):
    def send(self, mail: OutgoingMail) -> None: ...


class ConsoleMailer:
    """Zapisuje wiadomosc do pliku w `outbox_dir` i loguje link logowania (NIE uzywac w produkcji)."""

    def __init__(self, outbox_dir: Path) -> None:
        self.outbox_dir = outbox_dir

    def send(self, mail: OutgoingMail) -> None:
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        name = f"{stamp}-{hashlib.sha256(mail.to.encode()).hexdigest()[:12]}.json"
        path = self.outbox_dir / name
        payload = {"to": mail.to, "subject": mail.subject, "body": mail.body, "link": mail.link,
                   "created_at": time.time()}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        log.info("console mailer: message for %s written to outbox (%s)%s", mail.to, name,
                 f" link={mail.link}" if mail.link else "")


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.host = settings.daas_smtp_host or ""
        self.port = settings.daas_smtp_port
        self.user = settings.daas_smtp_user
        self.password = settings.daas_smtp_password
        self.starttls = settings.daas_smtp_starttls
        self.sender = settings.daas_email_from

    def send(self, mail: OutgoingMail) -> None:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = mail.to
        msg["Subject"] = mail.subject
        msg.set_content(mail.body)
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            if self.starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self.user:
                smtp.login(self.user, self.password or "")
            smtp.send_message(msg)


def build_mailer(settings: Settings) -> Mailer:
    if settings.daas_email_backend == "smtp":
        if not settings.daas_smtp_host:
            raise RuntimeError("DAAS_SMTP_HOST is required when DAAS_EMAIL_BACKEND=smtp")
        return SmtpMailer(settings)
    return ConsoleMailer(settings.outbox_dir)


def magic_link_mail(settings: Settings, to: str, token: str, locale: str) -> OutgoingMail:
    origin = (settings.daas_cockpit_origin or "http://localhost:3000").rstrip("/")
    link = f"{origin}/login/verify?token={token}"
    ttl = settings.daas_magic_link_ttl_minutes
    if locale == "en":
        subject = "Your DaaS Engine sign-in link"
        body = (f"Open this link to sign in (valid {ttl} minutes, single use):\n\n{link}\n\n"
                "If you did not request it, ignore this message.\n")
    else:
        subject = "Link do logowania w DaaS Engine"
        body = (f"Otwórz link, aby się zalogować (ważny {ttl} min, jednorazowy):\n\n{link}\n\n"
                "Jeśli to nie Ty prosiłeś o logowanie, zignoruj tę wiadomość.\n")
    return OutgoingMail(to=to, subject=subject, body=body, link=link)


def invitation_mail(settings: Settings, to: str, org_name: str, role: str, locale: str) -> OutgoingMail:
    origin = (settings.daas_cockpit_origin or "http://localhost:3000").rstrip("/")
    link = f"{origin}/login"
    if locale == "en":
        subject = f"You were invited to {org_name} on DaaS Engine"
        body = (f"You were invited to \"{org_name}\" as {role}.\n"
                f"Sign in with this e-mail address to join: {link}\n")
    else:
        subject = f"Zaproszenie do organizacji {org_name} w DaaS Engine"
        body = (f"Zaproszono Cię do organizacji „{org_name}” jako {role}.\n"
                f"Zaloguj się tym adresem e-mail, aby dołączyć: {link}\n")
    return OutgoingMail(to=to, subject=subject, body=body, link=link)
