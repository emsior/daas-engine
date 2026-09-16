"""Policy layer dla agentow: kontrakty narzedzi, allowlista rol, budzety, audit log.

Zasada naczelna: model jest planista, nigdy autorytetem. Autorytet mieszka tutaj.
Kazde wywolanie narzedzia przechodzi przez enforce() i laduje w append-only audit logu.

Warstwy:
    W1 kontrakty narzedzi   -> modele Pydantic z walidacja argumentow
    W2 policy / HITL        -> allowlista per rola + zgoda czlowieka na operacje nieodwracalne
    W4 budzety              -> limit krokow / kosztu / czasu + detektor petli
    W5 audit                -> <runtime>/audit.jsonl (append-only, hash wyniku)

Sciezka audytu ma dokladnie jedno zrodlo prawdy: Settings.audit_log_path.
Zaden call site nie sklada jej wlasnym literalem — inaczej log rozwarstwia sie
na kilka plikow w zaleznosci od tego, skad wywolano enforce() (dlug Z-10).

(W3 sandbox jest poza kodem: kontener / read-only mounty / egress allowlist.)
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, field_validator

# Sam literal nazwy pliku mieszka w konfiguracji — import stalej nie tworzy cyklu
# (config nie importuje niczego z app) i nie odpala get_settings() przy imporcie.
from app.core.config import AUDIT_LOG_FILENAME

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = (PROJECT_ROOT / "runtime").resolve()

#: Workspace aktywny dla biezacego wywolania enforce(); None = domyslne WORKSPACE.
#: Ustawiany przez enforce(workspace=...), zeby runtime (natywnie / Docker / tenant)
#: mogl podac wlasny katalog runtime zamiast stalej modulu.
_workspace_ctx: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "daas_policy_workspace", default=None
)


def active_workspace() -> Path:
    """Katalog roboczy obowiazujacy w tym wywolaniu policy (rozwiazany do sciezki absolutnej)."""
    ws = _workspace_ctx.get()
    return (ws if ws is not None else WORKSPACE).resolve()


#: Serializuje dopisywanie linii audytu w obrebie procesu (FastAPI threadpool,
#: runner, testy wielowatkowe). Jedna linia = jedno write() pod tym zamkiem.
_AUDIT_LOCK = threading.Lock()

#: Sciezka audytu uzywana, gdy konfiguracja jest niedostepna (policy uzyte samodzielnie).
#: Celowo w runtime/, a nie w osobnym logs/ — ten sam katalog, co sciezka kanoniczna.
FALLBACK_AUDIT_LOG = WORKSPACE / AUDIT_LOG_FILENAME


def default_audit_log() -> Path:
    """Kanoniczna sciezka audytu dla wywolan bez jawnego `audit_log`.

    Zrodlem prawdy jest Settings.audit_log_path. Import jest leniwy, zeby policy
    pozostalo modulem bez zaleznosci importowej od warstwy konfiguracji i zeby
    get_settings() nie odpalalo sie przy samym imporcie tego modulu.
    """
    try:
        from app.core.config import get_settings

        return get_settings().audit_log_path
    except Exception:  # noqa: BLE001 - brak konfiguracji nie moze uciszyc audytu
        return FALLBACK_AUDIT_LOG


def append_audit_line(audit_log: Path, event: dict[str, Any]) -> None:
    """Dopisuje jedno zdarzenie jako jedna linie JSON (append-only, thread-safe).

    Gwarancje:
        * tryb `a` — nigdy nie nadpisuje istniejacej tresci,
        * caly rekord leci jednym write(), wiec linie sie nie przeplataja,
        * serializacja poza zamkiem — zamek trzymany tylko na czas zapisu.
    """
    line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
    with _AUDIT_LOCK:
        audit_log.parent.mkdir(parents=True, exist_ok=True)
        with audit_log.open("a", encoding="utf-8") as fh:
            fh.write(line)


Role = Literal["researcher", "analyst", "executor"]

#: Narzedzia nieodwracalne — zawsze wymagaja zgody czlowieka (HITL gate).
DESTRUCTIVE: set[str] = {"send_email", "publish", "delete_file", "git_push", "charge"}

#: Allowlista: co wolno ktorej roli. Brak wpisu = brak uprawnien.
ALLOW: dict[str, set[str]] = {
    "researcher": {"web_search", "fetch_url", "read_file"},
    "analyst": {"read_file", "run_sql"},
    "executor": {"write_file", "send_email", "publish", "git_push"},
}


class PolicyError(RuntimeError):
    """Odmowa policy layer. Nigdy nie jest wyjatkiem technicznym — to decyzja."""


# --------------------------------------------------------------------------- W1
class ReadFile(BaseModel):
    """Odczyt pliku ograniczony do katalogu roboczego."""

    path: str

    @field_validator("path")
    @classmethod
    def inside_workspace(cls, v: str) -> str:
        root = active_workspace()
        p = Path(v)
        candidate = p.resolve() if p.is_absolute() else (root / p).resolve()
        if not candidate.is_relative_to(root):
            # Bez sciezek absolutnych w komunikacie - nie ujawniamy ukladu serwera.
            raise PolicyError("sciezka poza workspace")
        return str(candidate)


#: Klauzule DuckDB rozszerzajace dostep poza odczyt — blokowane w RunSQL.
BANNED_SQL: tuple[str, ...] = ("attach", "copy ", "install", "load ", "pragma", "export")


class RunSQL(BaseModel):
    """Zapytanie DuckDB tylko do odczytu, bez klauzul rozszerzajacych dostep."""

    query: str

    @field_validator("query")
    @classmethod
    def read_only(cls, v: str) -> str:
        q = v.strip().lower()
        if not q.startswith(("select", "with")):
            raise PolicyError("dozwolone tylko SELECT / WITH")
        if any(k in q for k in BANNED_SQL):
            raise PolicyError("zablokowana klauzula DuckDB")
        return v


# --------------------------------------------------------------------------- W4
@dataclass
class Budget:
    """Twardy limit na sesje agenta. Agent bez budzetu = karta kredytowa oddana modelowi."""

    max_steps: int = 25
    max_cost_usd: float = 2.00
    max_wall_s: int = 900
    steps: int = 0
    cost: float = 0.0
    t0: float = field(default_factory=time.time)
    _fingerprints: list[str] = field(default_factory=list)

    def charge(self, tool: str, args: dict[str, Any], usd: float = 0.0) -> None:
        self.steps += 1
        self.cost += usd
        if self.steps > self.max_steps:
            raise PolicyError(f"limit krokow ({self.max_steps})")
        if self.cost > self.max_cost_usd:
            raise PolicyError(f"limit kosztu ({self.max_cost_usd} USD)")
        if time.time() - self.t0 > self.max_wall_s:
            raise PolicyError(f"limit czasu ({self.max_wall_s}s)")

        payload = f"{tool}{json.dumps(args, sort_keys=True, default=str)}"
        self._fingerprints.append(hashlib.sha256(payload.encode()).hexdigest()[:16])
        if len(self._fingerprints) >= 3 and len(set(self._fingerprints[-3:])) == 1:
            raise PolicyError("wykryta petla — ta sama akcja 3x pod rzad")


# ---------------------------------------------------------------------- W2 + W5
def enforce(
    tool: str,
    raw_args: dict[str, Any],
    schema: type[BaseModel],
    fn: Callable[..., Any],
    budget: Budget,
    role: Role,
    allow: dict[str, set[str]] | None = None,
    approve: Callable[[str, dict[str, Any]], bool] | None = None,
    audit_log: Path | None = None,
    workspace: Path | None = None,
) -> Any:
    """Jedyna droga wywolania narzedzia przez agenta.

    Kolejnosc: allowlista -> walidacja argumentow -> budzet -> HITL -> wykonanie.
    Audit zapisuje sie ZAWSZE, takze przy odmowie (blok finally).
    """
    allow = ALLOW if allow is None else allow
    audit_log = default_audit_log() if audit_log is None else audit_log
    decision, result = "allow", None

    try:
        if tool not in allow.get(role, set()):
            decision = "deny:not_allowlisted"
            raise PolicyError(f"rola '{role}' nie ma uprawnien do '{tool}'")

        try:
            _tok = _workspace_ctx.set(workspace)
            try:
                args = schema(**raw_args).model_dump()
            finally:
                _workspace_ctx.reset(_tok)
        except PolicyError:
            decision = "deny:invalid_args"
            raise
        except Exception as exc:  # blad schematu = odmowa, nie crash agenta
            decision = "deny:schema"
            raise PolicyError(f"argumenty niezgodne z kontraktem: {exc}") from exc

        try:
            budget.charge(tool, args)
        except PolicyError:
            decision = "deny:budget"
            raise

        if tool in DESTRUCTIVE:
            if approve is None or not approve(tool, args):
                decision = "deny:no_human_approval"
                raise PolicyError(f"'{tool}' jest nieodwracalne i wymaga zgody czlowieka")

        result = fn(**args)
        return result

    finally:
        append_audit_line(
            audit_log,
            {
                "ts": round(time.time(), 3),
                "role": role,
                "tool": tool,
                "args": raw_args,
                "decision": decision,
                "result_sha": hashlib.sha256(repr(result).encode()).hexdigest()[:16],
                "steps": budget.steps,
                "cost_usd": round(budget.cost, 4),
            },
        )
