"""`GET /metrics` — metryki w formacie tekstowym Prometheus (0.0.4).

Dostep: `Authorization: Bearer <DAAS_METRICS_TOKEN>`. Gdy token nie jest ustawiony, trasa udaje, ze nie istnieje (404).
Etykiety nie zawieraja danych klienta (zob. `render_prometheus`).
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from app.api.problems import ProblemError
from app.api.v1.schemas import JobStatus
from app.control.models import Job
from app.core.config import installed_version
from app.core.observability import PROMETHEUS_CONTENT_TYPE, render_prometheus
from app.core.ratelimit import throttle_auth_failure

router = APIRouter(tags=["system"])

JOB_STATUSES: tuple[str, ...] = JobStatus.__args__  # type: ignore[attr-defined]


def _token_matches(presented: str, expected: str) -> bool:
    return hmac.compare_digest(hashlib.sha256(presented.encode()).digest(), hashlib.sha256(expected.encode()).digest())


@router.get(
    "/metrics", response_class=PlainTextResponse, summary="Prometheus metrics",
    description="Prometheus text format 0.0.4: requests per route template and status, a response-time histogram, "
                "jobs per status and the number of active tenants. Needs `Authorization: Bearer <metrics token>`; "
                "answers 404 when no token is configured. Labels never carry customer data.",
    responses={200: {"content": {"text/plain": {"schema": {"type": "string"}}}},
               401: {"description": "Missing or wrong token"}, 404: {"description": "Metrics are not enabled"}},
)
async def metrics(request: Request) -> PlainTextResponse:
    state = request.app.state
    expected = state.settings.daas_metrics_token
    if not expected or not expected.strip():
        raise ProblemError(404, "not_found", "not found")
    scheme, _, presented = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not presented.strip() or not _token_matches(presented.strip(), expected.strip()):
        throttle_auth_failure(request)
        raise ProblemError(401, "unauthorized", "authentication required", headers={"WWW-Authenticate": "Bearer"})
    async with state.db_sessionmaker() as db:
        rows = (await db.execute(select(Job.status, func.count()).group_by(Job.status))).all()
    counts = {status: 0 for status in JOB_STATUSES}
    counts.update({status: count for status, count in rows})
    body = render_prometheus(
        state.metrics, job_counts=counts, active_tenants=len(state.tenants.active()),
        version=installed_version(state.settings.app_version), mode=state.settings.daas_mode)
    return PlainTextResponse(body, media_type=PROMETHEUS_CONTENT_TYPE, headers={"Cache-Control": "no-store"})
