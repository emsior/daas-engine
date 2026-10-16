"""Zdarzenia audytowe z warstwy HTTP: jeden pomocnik dla `upload`, `run` i `auth_denied`.

Zapis idzie wspolnym kanalem (`upload_guard.append_audit` -> `policy.append_audit_line`), do jednego
pliku `Settings.audit_log_path`. Blad zapisu nie przerywa zapytania, ale jest glosno logowany:
audyt nie moze po cichu zniknac (log z request_id), a awaria dysku nie moze zamienic sie w 500 na kazdej trasie.
"""
from __future__ import annotations

import logging

from fastapi import Request

from app.core.config import Settings
from app.core.observability import get_request_id
from app.core.upload_guard import append_audit, build_audit_event, safe_deployment_id

log = logging.getLogger(__name__)


def deployment_id_for(settings: Settings) -> str:
    """Pseudonim wdrozenia (skrot sciezki uploads/); te sama wartosc co w audycie uploadow."""
    return safe_deployment_id(settings.uploads_path)


def record(request: Request, *, action: str, allowed: bool, reason: str, size_bytes: int = 0,
           upload_id: str | None = None, encoding: str | None = None, content_sha256: str | None = None,
           pipeline: str | None = None, run_id: str | None = None) -> None:
    """Dopisuje zdarzenie audytowe zapytania. Dodaje request_id, adres klienta i szablon trasy."""
    runner = getattr(request.app.state, "runner", None)
    settings: Settings = runner.settings if runner is not None else request.app.state.settings
    route = getattr(request.scope.get("route"), "path", None)
    try:
        append_audit(build_audit_event(
            allowed=allowed, reason=reason, deployment_id=deployment_id_for(settings),
            size_bytes=size_bytes, action=action, upload_id=upload_id, encoding=encoding,
            content_sha256=content_sha256, pipeline=pipeline, run_id=run_id,
            request_id=get_request_id(), client_ip=request.client.host if request.client else None,
            route=route or request.url.path[:200],
        ), settings.audit_log_path)
    except Exception:  # noqa: BLE001
        log.exception("audit write failed action=%s reason=%s", action, reason)
