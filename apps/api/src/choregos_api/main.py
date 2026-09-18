"""Application FastAPI de Choregos."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db.session import create_all, dispose_engine
from .errors import install_error_handlers
from .logging import bind, clear, configure_logging, get_logger
from .routers import (
    admin,
    auth,
    connectors,
    costs,
    findings,
    internal,
    memory,
    models,
    projects,
    runs,
    templates,
    trains,
    webhooks,
    workflows,
    workitems,
)

API_PREFIX = "/api/v1"
logger = get_logger("choregos.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    logger.info("démarrage de l'API", env=settings.env, fakes=settings.fakes)
    if settings.env in {"dev", "test"} or settings.is_sqlite:
        await create_all()  # en staging/prod, c'est Alembic qui pose le schéma
    yield
    await dispose_engine()
    logger.info("arrêt de l'API")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Choregos API",
        version="1.0.0",
        description="Un ticket entre, une mise en production maîtrisée sort.",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def logging_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        clear()
        bind(path=request.url.path, method=request.method)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("erreur non gérée", path=request.url.path)
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        if not request.url.path.startswith(("/healthz", "/readyz")):
            logger.info("requête", status=response.status_code, duration_ms=duration_ms)
        return response

    for router in (
        auth.router,
        projects.router,
        connectors.router,
        workflows.router,
        models.router,
        workitems.router,
        runs.router,
        trains.router,
        findings.router,
        memory.router,
        costs.router,
        templates.router,
        admin.router,
        webhooks.router,
        internal.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    @app.get("/healthz", tags=["session"], operation_id="healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["session"], operation_id="readyz")
    async def readyz() -> Any:
        """Prêt = la base répond. Sinon 503 : le pod ne prend pas de trafic."""
        from sqlalchemy import text

        from .db.session import get_engine

        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            return JSONResponse({"status": "not_ready", "detail": str(exc)[:200]}, status_code=503)
        return {"status": "ready"}

    return app


app = create_app()


def main() -> None:
    """Point d'entrée `choregos-api`."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "choregos_api.main:app",
        host="0.0.0.0",  # noqa: S104 - conteneur
        port=8000,
        reload=settings.env == "dev",
        log_config=None,
    )
