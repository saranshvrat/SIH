"""PixelForge API — application factory."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import APP_VERSION, settings
from app.core.errors import register_exception_handlers
from app.core.ids import request_id as new_request_id
from app.core.logging import bind, clear, configure_logging, get_logger

log = get_logger(__name__)

DESCRIPTION = """
Super-resolution and geospatial intelligence for medium-resolution satellite imagery.

**Data model.** PixelForge is a one-time processing application. There are no accounts.
Everything is scoped to an anonymous browser session and deleted on a TTL. See
`GET /api/v1/session` for exactly what is held and `DELETE /api/v1/session` to erase it.

**Honesty.** Metrics that require ground truth return `null` with an explanation when no
reference exists. Uncertainty is reported only when it can be computed from genuine model
disagreement, and is always flagged `calibrated: false` until validated. Models that are
not actually runnable are reported `unavailable` with a reason.
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.LOG_LEVEL, settings.LOG_JSON)
    from app.db.session import init_db
    if settings.ENVIRONMENT != "production":
        init_db()
    from app.ml.registry import available_model_keys
    log.info("startup", version=APP_VERSION, env=settings.ENVIRONMENT,
             storage=settings.STORAGE_DRIVER, models=available_model_keys(),
             catalog="planetary_computer", llm=settings.llm_enabled)
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="PixelForge API", version=APP_VERSION, description=DESCRIPTION,
                  docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json",
                  lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["*"],
        expose_headers=["X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or new_request_id()
        request.state.request_id = rid
        bind(request_id=rid, path=request.url.path, method=request.method)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            clear()
        response.headers["X-Request-ID"] = rid
        response.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
        return response

    register_exception_handlers(app)

    from app.api.v1.routes import (analysis, aois, artifacts, chat, health, jobs, models,
                                   reports, results, scenes, session_routes)
    p = settings.API_V1_PREFIX
    for module in (health, models, aois, scenes, jobs, results, artifacts,
                   session_routes, analysis, chat, reports):
        app.include_router(module.router, prefix=p)

    @app.get("/", include_in_schema=False)
    def root():
        return {"name": "PixelForge API", "version": APP_VERSION, "docs": "/docs",
                "health": f"{p}/health"}

    return app


app = create_app()
