"""Zakresy (scopes) kluczy API i uprawnienia rol. Jedno zrodlo prawdy dla API v1."""
from __future__ import annotations

from typing import Literal

Role = Literal["owner", "admin", "member"]
ROLES: tuple[str, ...] = ("owner", "admin", "member")

# --- zakresy ---
ORG_READ = "org:read"
ORG_MANAGE = "org:manage"
ORG_DELETE = "org:delete"
MEMBERS_MANAGE = "members:manage"
KEYS_MANAGE = "keys:manage"
UPLOADS_WRITE = "uploads:write"
RUNS_READ = "runs:read"
RUNS_WRITE = "runs:write"
REPORTS_READ = "reports:read"
USAGE_READ = "usage:read"
AUDIT_READ = "audit:read"
WEBHOOKS_MANAGE = "webhooks:manage"

#: Zakresy, ktore mozna nadac kluczowi API (org:delete jest tylko dla sesji wlasciciela).
KEY_SCOPES: tuple[str, ...] = (
    ORG_READ, ORG_MANAGE, MEMBERS_MANAGE, KEYS_MANAGE, UPLOADS_WRITE, RUNS_READ, RUNS_WRITE,
    REPORTS_READ, USAGE_READ, AUDIT_READ, WEBHOOKS_MANAGE,
)

_MEMBER = frozenset({ORG_READ, UPLOADS_WRITE, RUNS_READ, RUNS_WRITE, REPORTS_READ, USAGE_READ})
_ADMIN = _MEMBER | {ORG_MANAGE, MEMBERS_MANAGE, KEYS_MANAGE, AUDIT_READ, WEBHOOKS_MANAGE}
_OWNER = _ADMIN | {ORG_DELETE}

#: member = odczyt + upload/zadania; admin = + klucze, czlonkowie, webhooki, audyt; owner = + usuniecie org.
ROLE_SCOPES: dict[str, frozenset[str]] = {"member": _MEMBER, "admin": frozenset(_ADMIN), "owner": frozenset(_OWNER)}

#: Zakresy klucza wdrozenia z DAAS_API_KEY (tryb selfhost): pelne uprawnienia w organizacji `default`.
SELFHOST_KEY_SCOPES: frozenset[str] = frozenset(KEY_SCOPES)


def role_rank(role: str) -> int:
    return {"member": 1, "admin": 2, "owner": 3}.get(role, 0)
