"""Punkt wejścia FastAPI: logowanie JSON, middleware (request-id, nagłówki), auth, lifespan (DuckDB + runner)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import public_router, router
from app.core.auth import API_KEY_HEADER
from app.core.config import Settings, get_settings
from app.core.observability import (
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    configure_logging,
    get_request_id,
)
from app.core.startup import validate_startup
from app.pipelines.runner import PipelineRunner

#: Kod bledu wg statusu HTTP (stabilny, czytelny maszynowo). Reszta 4xx -> "client_error".
ERROR_CODES: dict[int, str] = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
    413: "payload_too_large", 415: "unsupported_media_type", 422: "unprocessable", 429: "rate_limited",
    500: "internal_error", 503: "unavailable",
}
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")


def _error_body(detail, code: str) -> dict:
    """Jednolity ksztalt bledu: detail (jak dotad), code, request_id."""
    return {"detail": detail, "code": code, "request_id": get_request_id()}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    auth = validate_startup(settings)  # fail-closed: RuntimeError przy zlej konfiguracji
    dev = settings.app_env.strip().lower() == "dev"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runner = PipelineRunner(settings)
        logging.getLogger("daas").info(
            "DaaS Engine up · env=%s · api_key=%s", settings.app_env, "set" if auth.configured else "MISSING"
        )
        yield

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Source-Agnostic B2B Data-as-a-Service Engine (FastAPI + DuckDB + n8n)",
        lifespan=lifespan,
        # /docs, /redoc i /openapi.json wylacznie przy APP_ENV=dev
        docs_url="/docs" if dev else None,
        redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.state.settings = settings
    app.state.auth = auth

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ERROR_CODES.get(exc.status_code, "client_error" if exc.status_code < 500 else "server_error")
        return JSONResponse(_error_body(exc.detail, code), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(_error_body(jsonable_encoder(exc.errors()), "validation_error"), status_code=422)

    # Kolejnosc: ostatnio dodane = najbardziej zewnetrzne. CORS (jesli wlaczony) jest najglebiej.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", API_KEY_HEADER],
        )
    app.add_middleware(RequestIdMiddleware)
    # Swagger UI potrzebuje wlasnych skryptow, wiec w dev wylaczamy dla nich domyslne CSP API.
    app.add_middleware(SecurityHeadersMiddleware, csp_exempt_paths=DOCS_PATHS if dev else ())
    app.include_router(public_router)
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    # workers=1 jawnie: DuckDB (single writer) i limiter w pamieci procesu nie wspolpracuja z wieloma procesami
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port, reload=s.app_env == "dev", workers=1)
