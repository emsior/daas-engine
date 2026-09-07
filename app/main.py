"""Punkt wejścia FastAPI: CORS, logowanie, lifespan (DuckDB + runner), routery."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.pipelines.runner import PipelineRunner


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runner = PipelineRunner(settings)
        logging.getLogger("daas").info(
            "DaaS Engine up · db=%s · missing secrets=%s", settings.duckdb_file, settings.missing_secrets()
        )
        yield

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Source-Agnostic B2B Data-as-a-Service Engine (FastAPI + DuckDB + n8n)",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port, reload=s.app_env == "dev")
