"""Bledy API v1 w formacie RFC 9457 (`application/problem+json`).

Pola standardowe: `type`, `title`, `status`, `detail`, `instance`; rozszerzenia: `request_id` oraz `code`
(stabilny kod maszynowy, ten sam co w odpowiedziach legacy). `type` to URN `urn:daas:problem:<code>`.
`detail` nigdy nie niesie sciezek, tresci plikow ani wartosci z zadania.
"""
from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

PROBLEM_MEDIA_TYPE = "application/problem+json"
API_V1_PREFIX = "/api/v1"

#: Kod bledu wg statusu HTTP (stabilny, czytelny maszynowo). Reszta 4xx -> "client_error".
ERROR_CODES: dict[int, str] = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
    409: "conflict", 413: "payload_too_large", 415: "unsupported_media_type", 422: "unprocessable",
    429: "rate_limited", 500: "internal_error", 503: "unavailable",
}


class ValidationIssue(BaseModel):
    """Redacted request validation error, without the submitted value."""

    loc: list[str]
    msg: str
    type: str


class Problem(BaseModel):
    """Shared RFC 9457 response contract, including optional error extensions."""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str
    instance: str
    request_id: str | None
    code: str
    errors: list[ValidationIssue] | None = None


class ProblemError(HTTPException):
    """Wyjatek z kodem maszynowym i opcjonalnymi rozszerzeniami; w trasach legacy dziala jak zwykly HTTPException."""

    def __init__(self, status_code: int, code: str, detail: str, *, headers: dict[str, str] | None = None,
                 extra: dict[str, Any] | None = None) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.extra = extra or {}


def code_for(status: int, exc: Exception | None = None) -> str:
    explicit = getattr(exc, "code", None)
    if isinstance(explicit, str):
        return explicit
    return ERROR_CODES.get(status, "client_error" if status < 500 else "server_error")


def problem_body(status: int, code: str, detail: str | None, instance: str, request_id: str | None,
                 extra: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        title = HTTPStatus(status).phrase
    except ValueError:
        title = "Error"
    body: dict[str, Any] = {
        "type": f"urn:daas:problem:{code.replace('_', '-')}",
        "title": title,
        "status": status,
        "detail": detail if isinstance(detail, str) else title,
        "instance": instance,
        "request_id": request_id,
        "code": code,
    }
    if extra:
        body.update({k: v for k, v in extra.items() if k not in body})
    return body


def is_v1_path(path: str) -> bool:
    return path == API_V1_PREFIX or path.startswith(API_V1_PREFIX + "/")


#: Endpoint serwera MCP (Streamable HTTP). Bledy HTTP tej sciezki tez sa w formacie problem+json.
MCP_PATH = "/mcp"


def is_problem_path(path: str) -> bool:
    """Sciezki z bledami `application/problem+json`: API v1 oraz /mcp (takze gdy MCP jest wylaczone -> 404)."""
    return is_v1_path(path) or path == MCP_PATH or path.startswith(MCP_PATH + "/")


def not_found(what: str = "resource") -> ProblemError:
    return ProblemError(404, "not_found", f"{what} not found")


def forbidden(detail: str = "insufficient permissions", code: str = "forbidden") -> ProblemError:
    return ProblemError(403, code, detail)
