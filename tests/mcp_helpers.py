"""Pomocnicy testow serwera MCP (`/mcp`): budowa komunikatow JSON-RPC, naglowki i wywolania narzedzi."""
from __future__ import annotations

import itertools
import time
from typing import Any

from fastapi.testclient import TestClient

MCP = "/mcp"
#: Wersja protokolu z handshake `initialize` (era 2025); SDK obsluguje tez 2026-07-28 bez handshake.
PROTOCOL = "2025-11-25"
ACCEPT = "application/json, text/event-stream"
TOOL_NAMES = ("whoami", "list_pipelines", "list_uploads", "upload_csv", "start_run", "get_job", "list_runs",
              "get_run", "get_report", "verify_report", "get_evidence", "get_usage")

_ids = itertools.count(1)


def mcp_headers(key: str | None = None, *, scheme: str = "bearer", protocol: str | None = PROTOCOL) -> dict[str, str]:
    """Naglowki zadania MCP. `scheme`: `bearer` (`Authorization`) albo `x-api-key`."""
    headers = {"Accept": ACCEPT, "Content-Type": "application/json"}
    if protocol:
        headers["MCP-Protocol-Version"] = protocol
    if key is not None:
        if scheme == "bearer":
            headers["Authorization"] = f"Bearer {key}"
        else:
            headers["X-API-Key"] = key
    return headers


def rpc(method: str, params: dict[str, Any] | None = None, *, id: int | None = None) -> dict[str, Any]:  # noqa: A002
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id if id is not None else next(_ids), "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize_params() -> dict[str, Any]:
    return {"protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "daas-tests", "version": "1.0"}}


def post(client: TestClient, message: dict[str, Any], key: str | None, *, extra: dict[str, str] | None = None,
         **kwargs):
    headers = {**mcp_headers(key, **kwargs), **(extra or {})}
    return client.post(MCP, json=message, headers=headers)


def handshake(client: TestClient, key: str) -> dict[str, Any]:
    """`initialize` -> `notifications/initialized`; zwraca wynik `initialize`."""
    r = post(client, rpc("initialize", initialize_params()), key, protocol=None)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    ack = post(client, notification("notifications/initialized"), key)
    assert ack.status_code == 202, ack.text
    return result


def call_tool(client: TestClient, key: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """`tools/call`; zwraca `result` (CallToolResult jako dict). Bledy JSON-RPC i HTTP koncza test."""
    r = post(client, rpc("tools/call", {"name": name, "arguments": arguments or {}}), key)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json"), r.headers["content-type"]
    body = r.json()
    assert "result" in body, body
    return body["result"]


def text_of(result: dict[str, Any]) -> str:
    return "\n".join(c["text"] for c in result.get("content", []) if c.get("type") == "text")


def ok(result: dict[str, Any]) -> dict[str, Any]:
    """`structuredContent` udanego wywolania."""
    assert not result.get("isError"), text_of(result)
    return result["structuredContent"]


def error_code(result: dict[str, Any]) -> str:
    """Kod bledu narzedzia (`code: message`)."""
    assert result.get("isError") is True, result
    return text_of(result).split(":", 1)[0]


def wait_job(client: TestClient, key: str, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    job: dict[str, Any] = {}
    while time.monotonic() < deadline:
        job = ok(call_tool(client, key, "get_job", {"job_id": job_id}))
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {job}")
