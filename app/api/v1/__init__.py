"""API v1 (`/api/v1`): routery zasobow. Zob. `build_router`."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api import routes as system_routes
from app.api.v1 import auth
from app.core.models import HealthResponse, ReadyResponse
from app.core.ratelimit import limit_ip

API_PREFIX = "/api/v1"


def build_router() -> APIRouter:
    """Zbiera routery v1. Limit per IP obejmuje wszystkie trasy (takze publiczne trasy logowania)."""
    router = APIRouter(prefix=API_PREFIX, dependencies=[Depends(limit_ip)])
    # te same handlery co /health i /ready (kontrakt B1 zostaje), dostepne tez pod prefiksem wersji
    router.add_api_route("/health", system_routes.health, methods=["GET"], response_model=HealthResponse,
                         tags=["system"], summary="Liveness (public)")
    router.add_api_route("/ready", system_routes.ready, methods=["GET"], response_model=ReadyResponse,
                         tags=["system"], summary="Readiness (public)", responses={503: {"model": ReadyResponse}})
    router.include_router(auth.public_router)
    router.include_router(auth.router)
    return router
