"""Pierwotniaki bezpieczenstwa control plane: klucze API, tokeny, podpis sesji, podpis webhookow.

Zasady:
    * Klucz API ma format `daas_<prefiks 8 hex>_<sekret>`; w bazie jest wylacznie prefiks i skrot SHA-256.
      Klucze maja >= 256 bitow entropii, wiec nieslony SHA-256 wystarcza (brak slownika do odgadniecia).
    * Tokeny (magic link, zaproszenia) to 32 losowe bajty; w bazie tylko skrot SHA-256.
    * Cookie sesji = `<id>.<HMAC-SHA256(DAAS_SECRET_KEY, id)>`; porownanie przez `hmac.compare_digest`.
    * Sekret podpisu webhooka jest pochodna `DAAS_SECRET_KEY` i id webhooka (nie lezy w bazie).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

KEY_PREFIX_LEN = 8
KEY_BRAND = "daas_"
_KEY_RE = re.compile(r"^daas_([0-9a-f]{8})_[A-Za-z0-9_-]{32,128}$")


def sha256_hex(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def safe_equal(a: str, b: str) -> bool:
    """Porownanie w stalym czasie dwoch napisow (skrotow hex)."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def new_api_key() -> tuple[str, str, str]:
    """Zwraca `(klucz, prefiks, skrot)`. Klucz wolno pokazac uzytkownikowi dokladnie raz."""
    prefix = secrets.token_hex(KEY_PREFIX_LEN // 2)
    raw = f"{KEY_BRAND}{prefix}_{secrets.token_urlsafe(32)}"
    return raw, prefix, sha256_hex(raw)


def parse_api_key(presented: str | None) -> str | None:
    """Zwraca prefiks (8 hex) z klucza w formacie `daas_...`, albo None gdy format jest inny."""
    if not presented:
        return None
    match = _KEY_RE.match(presented.strip())
    return match.group(1) if match else None


def new_token() -> tuple[str, str]:
    """Token jednorazowy: `(token, skrot)`; 32 bajty losowe, URL-safe."""
    token = secrets.token_urlsafe(32)
    return token, sha256_hex(token)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def _hmac(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_session_id(session_id: str, secret: str) -> str:
    return f"{session_id}.{_hmac(secret, 'session:' + session_id)}"


def unsign_session_cookie(cookie: str | None, secret: str | None) -> str | None:
    """Zwraca id sesji, gdy podpis jest poprawny; inaczej None. Nie rzuca wyjatkow."""
    if not cookie or not secret or "." not in cookie:
        return None
    session_id, _, signature = cookie.rpartition(".")
    if not session_id or not safe_equal(signature, _hmac(secret, "session:" + session_id)):
        return None
    return session_id


def webhook_signing_secret(webhook_id: str, secret: str) -> str:
    """Sekret podpisu webhooka (pokazywany raz przy utworzeniu). Odtwarzalny tylko z DAAS_SECRET_KEY."""
    return "whsec_" + _hmac(secret, "webhook:" + webhook_id)


def sign_payload(body: bytes, signing_secret: str) -> str:
    """Wartosc naglowka `X-DaaS-Signature`: `sha256=<hex HMAC ciala zadania>`."""
    digest = hmac.new(signing_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
