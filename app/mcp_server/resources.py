"""Zasoby MCP (resources) DaaS Engine: raport runu (Markdown, JSON), status artefaktow i podsumowanie uploadu.

Agent podpina raport jak plik (`@zasob` w Claude, Cursor, ChatGPT) zamiast szukac narzedzia. Kazdy zasob dziala
w organizacji klucza z `/mcp` (podmiot z `ctx.request_context.request.state.principal`, jak w narzedziach): organizacja
nigdy nie pochodzi z URI, a identyfikator obcej organizacji wyglada jak nieistniejacy (`not_found`). Tresc to TE SAME
funkcje co narzedzia i API v1 (`get_report`, `get_evidence`, `read_report_file`), wiec raport ma ten sam SHA-256.

Szablony URI (RFC 6570, `SPECS`) rejestruje `build_mcp` dekoratorem SDK `MCPServer.resource(...)`: konstruktor
`MCPServer(resources=...)` przyjmuje tylko zasoby konkretne, a SDK wstawia `Context` wylacznie do funkcji szablonow.
`resources/list` w SDK zwraca stala liste bez `Context`, dlatego 20 najnowszych raportow organizacji dopisuje do niej
middleware `resource_list_gate` (zadanie HTTP z podmiotem ma tylko middleware) — bez stanu globalnego.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mcp.server.mcpserver import Context
from mcp_types import Resource as McpResource
from pydantic import BaseModel
from sqlalchemy import select
from starlette.requests import Request

from app.api.v1 import runs as v1_runs
from app.api.v1 import uploads as v1_uploads
from app.api.v1.deps import Principal
from app.api.v1.schemas import PipelineId, UploadKind
from app.control import scopes as sc
from app.control.models import RunRecord, Upload
from app.core.observability import get_request_id
from app.mcp_server import tools as mcp_tools
from app.mcp_server.tools import (
    UNTRUSTED,
    RpcFailure,
    ToolFailure,
    call_of,
    principal_of,
    rpc_guarded,
)

log = logging.getLogger(__name__)

SCHEME = "daas://"
#: Najwiekszy raport JSON serwowany jako zasob (JSON nie jest obcinany: uciety JSON nie parsuje sie).
MAX_JSON_BYTES = 2 * 1024 * 1024
#: Ile najnowszych raportow organizacji pokazuje `resources/list`.
LIST_LIMIT = 20

# wzorce identyfikatorow jak w narzedziach (`tools.RunId`, `start_run.upload_id`)
_RUN_ID = re.compile(r"^[0-9A-Za-z-]{1,36}$")
_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
#: Konkretny URI -> szablon bez identyfikatora (`daas://runs/<id>/report.md` -> `runs/report.md`).
_URI_SHAPE = re.compile(r"^daas://([a-z]+)/[^/?#]+/([a-z]+\.[a-z]+)$")


class UploadSummary(BaseModel):
    """Metadane uploadu bez tresci pliku; `filename` pochodzi od klienta."""

    id: str
    kind: UploadKind
    filename: str
    bytes: int
    sha256: str
    created_at: datetime
    suggested_pipeline: PipelineId


def _check(value: str, pattern: re.Pattern[str], field: str) -> None:
    if not pattern.fullmatch(value):
        raise ToolFailure("validation_error", f"{field}: invalid format")


def _mib(size: int) -> str:
    return f"{size / (1024 * 1024):g} MiB"


def _num(value: int) -> str:
    """Liczba calkowita w konwencji PL: `200 000` (jak w narzedziach)."""
    return f"{value:,}".replace(",", " ")


# ============================================================================ funkcje szablonow
async def run_report_markdown(run_id: str, ctx: Context) -> str:
    """Tresc narzedzia `get_report` (format markdown): ten sam plik, hash i nota o obcieciu."""
    _check(run_id, _RUN_ID, "run_id")
    result = await mcp_tools.get_report(ctx, run_id=run_id, format="markdown")
    return result.structured_content["content"]


async def run_report_json(run_id: str, ctx: Context) -> str:
    """Caly raport JSON (bez obcinania); wiekszy niz `MAX_JSON_BYTES` -> `report_too_large`."""
    _check(run_id, _RUN_ID, "run_id")
    call = call_of(ctx)
    async with call.db() as db:
        _, body, _ = await v1_runs.read_report_file(call.state, db, call.principal.org_id, run_id, "json")
    if len(body) > MAX_JSON_BYTES:
        raise ToolFailure("report_too_large", f"the JSON report exceeds {_mib(MAX_JSON_BYTES)}; download it "
                                              f"through the REST API")
    return body.decode("utf-8", errors="replace")


async def run_evidence(run_id: str, ctx: Context) -> str:
    """Wynik `get_evidence` jako JSON (tylko runy `uksc_evidence`, inaczej `not_found`)."""
    _check(run_id, _RUN_ID, "run_id")
    result = await mcp_tools.get_evidence(ctx, run_id=run_id)
    return json.dumps(result.structured_content, ensure_ascii=False, indent=2)


async def upload_summary(upload_id: str, ctx: Context) -> str:
    """Metadane uploadu organizacji (bez tresci pliku)."""
    _check(upload_id, _UPLOAD_ID, "upload_id")
    call = call_of(ctx)
    async with call.db() as db:
        row = (await db.execute(select(Upload).where(
            Upload.id == upload_id, Upload.org_id == call.principal.org_id))).scalar_one_or_none()
    if row is None:
        raise ToolFailure("not_found", "upload not found")
    out = UploadSummary(id=row.id, kind=row.kind, filename=row.filename, bytes=row.bytes, sha256=row.sha256,
                        created_at=row.created_at, suggested_pipeline=v1_uploads.SUGGESTED_PIPELINE[row.kind])
    return out.model_dump_json(indent=2)


# ============================================================================ rejestr
@dataclass(frozen=True)
class ResourceSpec:
    """Szablon zasobu: funkcja (z `Context`), szablon URI, metadane MCP i wymagany zakres klucza."""

    fn: Callable[..., Awaitable[str]]
    uri_template: str
    title: str
    mime_type: str
    scope: str
    description: str

    @property
    def name(self) -> str:
        return self.fn.__name__

    @property
    def shape(self) -> str:
        """Szablon bez identyfikatora (`runs/report.md`): nazwa zasobu w audycie `mcp.resource`."""
        collection, _, rest = self.uri_template.removeprefix(SCHEME).partition("/")
        return f"{collection}/{rest.partition('/')[2]}"


SPECS: tuple[ResourceSpec, ...] = (
    ResourceSpec(run_report_markdown, "daas://runs/{run_id}/report.md", "Run report (Markdown)", "text/markdown",
                 sc.REPORTS_READ,
                 f"Markdown report of one run: the same content and SHA-256 as get_report; content longer than "
                 f"{_num(mcp_tools.MAX_REPORT_CHARS)} characters is cut with a note. {UNTRUSTED}"),
    ResourceSpec(run_report_json, "daas://runs/{run_id}/report.json", "Run report (JSON)", "application/json",
                 sc.REPORTS_READ,
                 f"Full JSON report of one run (never cut, the same SHA-256 as get_report with format json); a "
                 f"report over {_mib(MAX_JSON_BYTES)} answers report_too_large. {UNTRUSTED}"),
    ResourceSpec(run_evidence, "daas://runs/{run_id}/evidence.json", "Artifact status (JSON)", "application/json",
                 sc.REPORTS_READ,
                 f"Control, requirement, status and artifact hash list of a uksc_evidence run (as get_evidence), "
                 f"without artifact contents; runs of other pipelines answer not_found. {UNTRUSTED}"),
    ResourceSpec(upload_summary, "daas://uploads/{upload_id}/summary.json", "Upload summary (JSON)",
                 "application/json", sc.RUNS_READ,
                 f"Metadata of one upload (id, kind, filename, bytes, sha256, created_at, suggested_pipeline), never "
                 f"the file content; the file name comes from the customer. {UNTRUSTED}"),
)

_SHAPES = frozenset(spec.shape for spec in SPECS)


def build_resources() -> list[ResourceSpec]:
    """Szablony z funkcjami opakowanymi `rpc_guarded` (zakres klucza, bledy jako `RpcFailure`). `build_mcp` rejestruje
    je dekoratorem SDK: `server.resource(spec.uri_template, name=..., ...)(spec.fn)`."""
    return [ResourceSpec(rpc_guarded(spec.fn, scope=spec.scope, target=f"mcp:{spec.shape}"), spec.uri_template,
                         spec.title, spec.mime_type, spec.scope, spec.description) for spec in SPECS]


def audit_name(uri: str) -> str | None:
    """Szablon zasobu (`runs/report.md`) dla konkretnego URI albo None (URI spoza szablonow); do audytu bramki."""
    match = _URI_SHAPE.fullmatch(uri)
    if match is None:
        return None
    shape = f"{match[1]}/{match[2]}"
    return shape if shape in _SHAPES else None


# ============================================================================ resources/list
async def list_report_resources(request: Request, principal: Principal) -> list[dict[str, Any]]:
    """`LIST_LIMIT` najnowszych runow organizacji z raportem Markdown na dysku (hash jest, retencja nie usunela plikow)
    jako zasoby `report.md` (format wire `Resource`), bez tresci. Klucz bez `runs:read` albo `reports:read`: pusta lista."""
    if sc.RUNS_READ not in principal.scopes or sc.REPORTS_READ not in principal.scopes:
        return []
    async with request.app.state.db_sessionmaker() as db:
        rows = (await db.execute(select(RunRecord).where(
            RunRecord.org_id == principal.org_id, RunRecord.sha256_md.is_not(None),
            RunRecord.report_purged_at.is_(None),
        ).order_by(RunRecord.created_at.desc(), RunRecord.run_id.desc()).limit(LIST_LIMIT))).scalars().all()
    return [McpResource(
        uri=f"{SCHEME}runs/{r.run_id}/report.md", name=f"{r.pipeline} {r.created_at:%Y-%m-%d} {r.status}",
        description=f"Markdown report of run {r.run_id}, sha256 {r.sha256_md}. {UNTRUSTED}",
        mime_type="text/markdown",
    ).model_dump(by_alias=True, mode="json", exclude_none=True) for r in rows]


async def resource_list_gate(ctx, call_next):
    """Middleware SDK: do wyniku `resources/list` (stala lista SDK, u nas pusta) dopisuje raporty organizacji podmiotu.

    Zawsze `call_next` (koperte buduje SDK); blad odczytu -> `internal_error` bez szczegolow (log z request_id)."""
    if ctx.method != "resources/list" or ctx.request_id is None:
        return await call_next(ctx)
    result = await call_next(ctx)
    request = ctx.request if isinstance(ctx.request, Request) else None
    principal = principal_of(request)
    if request is None or principal is None or not isinstance(result, dict):
        return result
    try:
        listed = await list_report_resources(request, principal)
    except Exception:  # noqa: BLE001 - granica metody: szczegoly tylko w logu
        log.exception("mcp resources/list failed")
        raise RpcFailure("internal_error", f"unexpected server error (request_id {get_request_id()})") from None
    result["resources"] = [*(result.get("resources") or []), *listed]
    return result
