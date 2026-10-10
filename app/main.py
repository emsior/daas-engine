"""Punkt wejścia FastAPI: logowanie JSON, middleware (request-id, nagłówki), auth, lifespan.

Lifespan: silnik control plane (+ migracje), organizacja `default` (selfhost), rejestr najemcow (data plane).
Tryb `selfhost` montuje takze trasy legacy (`/upload`, `/run`, ...); tryb `cloud` — wylacznie `/api/v1`."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.problems import PROBLEM_MEDIA_TYPE, code_for, is_v1_path, problem_body
from app.api.routes import LEGACY_PREFIXES, public_router, router
from app.api.v1 import build_router as build_v1_router
from app.api.v1.metrics import router as metrics_router
from app.control import db as control_db
from app.control import service as control_service
from app.control.executor import JobExecutor
from app.control.mailer import build_mailer
from app.control.tenancy import TenantRegistry
from app.control.webhooks import WebhookDispatcher
from app.core.auth import API_KEY_HEADER
from app.core.config import Settings, get_settings, installed_version
from app.core.observability import (
    DeprecationMiddleware,
    Metrics,
    MetricsMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    configure_logging,
    get_request_id,
)
from app.core.ratelimit import RateLimiter
from app.core.startup import validate_startup
from app.core.upload_guard import cleanup_stale_tmp

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
        log = logging.getLogger("daas")
        # --- control plane: silnik, migracje, organizacja `default` (selfhost) ---
        if settings.daas_auto_migrate:
            await control_db.upgrade(settings)
        engine = control_db.create_engine(settings)
        app.state.db_engine = engine
        app.state.db_sessionmaker = control_db.make_sessionmaker(engine)
        if not settings.cloud:
            async with app.state.db_sessionmaker() as db:
                await control_service.ensure_default_org(db, retention_days=settings.daas_retention_days)
                await db.commit()
        # --- data plane ---
        registry = TenantRegistry(settings)
        app.state.tenants = registry
        if settings.cloud:
            app.state.runner = None
        else:
            _warn_if_unmigrated(settings)
            app.state.runner = await registry.aget("default")
            # po twardym przerwaniu procesu moga zostac pliki *.tmp (jeden worker = zaden upload nie trwa)
            removed = cleanup_stale_tmp(registry.paths("default").uploads, max_age_sec=0)
            if removed:
                log.info("removed %d leftover upload temp files", removed)
        if settings.cloud and settings.daas_email_backend == "console":
            log.warning("DAAS_EMAIL_BACKEND=console: sign-in links are written to runtime/outbox/ and the log; "
                        "use smtp in production")
        executor = JobExecutor(settings, app.state.db_sessionmaker, registry)
        app.state.executor = executor
        # dostawa webhookow `run.finished`: listener executora, dostawy w tle (nie blokuja runu)
        dispatcher = WebhookDispatcher(settings, app.state.db_sessionmaker)
        app.state.webhooks = dispatcher
        executor.listeners.append(dispatcher.on_run_finished)
        await executor.start()
        log.info(
            "DaaS Engine up · mode=%s · env=%s · api_key=%s", settings.daas_mode, settings.app_env,
            "set" if auth.configured else ("n/a" if settings.cloud else "MISSING"),
        )
        try:
            yield
        finally:
            await executor.stop()
            await dispatcher.close()
            registry.close_all()
            await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=installed_version(settings.app_version),
        description="Source-Agnostic B2B Data-as-a-Service Engine (FastAPI + DuckDB + n8n)",
        lifespan=lifespan,
        # /docs, /redoc i /openapi.json wylacznie przy APP_ENV=dev
        docs_url="/docs" if dev else None,
        redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.state.settings = settings
    app.state.auth = auth
    app.state.limiter = RateLimiter()
    app.state.metrics = Metrics()
    app.state.mailer = build_mailer(settings)
    app.state.runner = None  # ustawiane w lifespan (selfhost); w cloud zostaje None

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if is_v1_path(request.url.path):
            code = code_for(exc.status_code, exc)
            body = problem_body(exc.status_code, code, exc.detail if isinstance(exc.detail, str) else None,
                                request.url.path, get_request_id(), getattr(exc, "extra", None))
            return JSONResponse(body, status_code=exc.status_code, media_type=PROBLEM_MEDIA_TYPE,
                                headers=getattr(exc, "headers", None))
        code = code_for(exc.status_code)
        return JSONResponse(_error_body(exc.detail, code), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        if is_v1_path(request.url.path):
            # tylko lokalizacja, komunikat i typ: wartosc przeslana przez klienta nie jest odbijana
            errors = [{"loc": [str(x) for x in e.get("loc", ())], "msg": e.get("msg"), "type": e.get("type")}
                      for e in exc.errors()]
            body = problem_body(422, "validation_error", "request validation failed", request.url.path,
                                get_request_id(), {"errors": errors})
            return JSONResponse(body, status_code=422, media_type=PROBLEM_MEDIA_TYPE)
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
    if settings.daas_cockpit_origin:
        # kokpit: jeden origin, z credentials (cookie sesji); zadne inne pochodzenie nie dostaje CORS
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.daas_cockpit_origin.rstrip("/")],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", API_KEY_HEADER, "X-CSRF-Token"],
            expose_headers=["X-Request-ID", "Location", "ETag"],
        )
    app.add_middleware(MetricsMiddleware, metrics=app.state.metrics)
    if not settings.cloud:
        app.add_middleware(DeprecationMiddleware, paths=LEGACY_PREFIXES)
    app.add_middleware(RequestIdMiddleware)
    # Swagger UI potrzebuje wlasnych skryptow, wiec w dev wylaczamy dla nich domyslne CSP API.
    app.add_middleware(SecurityHeadersMiddleware, csp_exempt_paths=DOCS_PATHS if dev else ())
    app.include_router(public_router)
    app.include_router(build_v1_router())
    app.include_router(metrics_router)  # GET /metrics: poza /api/v1, wlasny token (404 gdy nieustawiony)
    if not settings.cloud:
        app.include_router(router)  # trasy legacy: tylko selfhost; w cloud odpowiadaja 404
    return app


def _warn_if_unmigrated(settings: Settings) -> None:
    """Instalacja sprzed 2.0 ma dane w runtime/ — po aktualizacji trzeba je przeniesc do runtime/tenants/default/."""
    legacy = settings.duckdb_file
    target = settings.tenants_root / "default" / "daas.duckdb"
    if legacy.exists() and not target.exists() and legacy.resolve() != target.resolve():
        logging.getLogger("daas").warning(
            "found a pre-2.0 database (%s) and no tenant database: run `python -m app.cli migrate-runtime`",
            legacy.name)


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    # workers=1 jawnie: DuckDB (single writer) i limiter w pamieci procesu nie wspolpracuja z wieloma procesami
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port, reload=s.app_env == "dev", workers=1)
