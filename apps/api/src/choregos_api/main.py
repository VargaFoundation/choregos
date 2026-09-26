"""Application FastAPI de Choregos."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from choregos_adapters import charger_les_greffons
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .adaptateurs import brancher_memoire_lexicale
from .amorcage import amorcer
from .config import get_settings
from .db.session import create_all, dispose_engine
from .errors import install_error_handlers
from .events import RelaisPostgres
from .logging import bind, clear, configure_logging, get_logger
from .metriques import boucle_de_rafraichissement, exposer, requetes_http
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
    if not settings.is_sqlite:
        await _refuser_le_superutilisateur()
    rafraichissement = asyncio.create_task(boucle_de_rafraichissement(settings.metrics_refresh_seconds))
    relais = None if settings.is_sqlite else RelaisPostgres(settings.database_url)
    if relais is not None:
        relais.demarrer()
    if not settings.is_sqlite:
        await amorcer(settings)
    if settings.is_sqlite:
        # SQLite seul : une base de fichier ou de mémoire, jetée avec le processus, qu'aucune
        # migration ne suit. Partout ailleurs le schéma vient d'Alembic — y compris en `dev`.
        # Le contraire créait les tables au démarrage de l'API, et la migration suivante
        # tombait sur « relation "agent_backends" already exists » : le déploiement échouait
        # en accusant les migrations, alors que c'est l'API qui avait pris leur place.
        await create_all()
        await amorcer(settings)
    yield
    if relais is not None:
        await relais.arreter()
    rafraichissement.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await rafraichissement
    await dispose_engine()
    logger.info("arrêt de l'API")


async def _refuser_le_superutilisateur() -> None:
    """Un superutilisateur PostgreSQL ignore la RLS : avec lui, l'isolation des organisations
    n'existe pas, quoi que disent les politiques. On le dit au démarrage, fort — et on refuse
    hors développement, parce qu'une plateforme multi-locataire sans isolation ment."""
    from sqlalchemy import text

    from .db.session import get_engine

    async with get_engine().connect() as conn:
        superuser = (
            await conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
        ).scalar()
    if superuser:
        message = (
            "l'API se connecte à PostgreSQL en SUPERUTILISATEUR : la RLS ne s'applique pas, "
            "les organisations ne sont pas isolées. Utiliser un rôle applicatif sans SUPERUSER."
        )
        if get_settings().env in {"staging", "prod"}:
            raise RuntimeError(message)
        logger.error(message)


def create_app() -> FastAPI:
    settings = get_settings()
    brancher_memoire_lexicale()
    # Les greffons installés à côté (édition entreprise, connecteurs maison) s'enregistrent
    # eux-mêmes. Un greffon déclaré qui ne charge pas arrête le démarrage, exprès.
    charger_les_greffons()
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
    from .limiteur import installer as installer_le_limiteur

    installer_le_limiteur(app, settings.rate_limit_per_minute)

    @app.middleware("http")
    async def logging_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        clear()
        # Un identifiant par requête, repris de l'entrée s'il y en a un (ingress, client),
        # rendu en réponse : c'est ce qui relie un journal de l'API à un journal du front.
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        bind(path=request.url.path, method=request.method, request_id=request_id)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("erreur non gérée", path=request.url.path)
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        response.headers["X-Request-Id"] = request_id
        route = getattr(request.scope.get("route"), "path", request.url.path)
        requetes_http.labels(request.method, route, str(response.status_code)).inc()
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

    @app.get("/metrics", tags=["session"], operation_id="metrics", include_in_schema=False)
    async def metrics() -> Response:
        """Les séries que les tableaux de bord et les alertes lisent (voir `metriques.py`)."""
        return Response(content=exposer(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/healthz", tags=["session"], operation_id="healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["session"], operation_id="readyz")
    async def readyz() -> Any:
        """Prêt = la base ET Temporal répondent. Sinon 503 : le pod ne prend pas de trafic.

        Temporal ne l'était pas : un pod dont Temporal était injoignable restait dans le
        service et rendait 500 sur chaque décision, chaque train, chaque webhook.
        """
        from sqlalchemy import text

        from .db.session import get_engine
        from .temporal import RealTemporal, get_temporal

        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            return JSONResponse(
                {"status": "not_ready", "detail": f"base : {str(exc)[:200]}"}, status_code=503
            )
        temporal = get_temporal()
        if isinstance(temporal, RealTemporal):
            try:
                client = await asyncio.wait_for(temporal.client(), timeout=2.0)
                if not await asyncio.wait_for(client.service_client.check_health(), timeout=2.0):
                    raise RuntimeError("check_health a répondu non")
            except Exception as exc:
                return JSONResponse(
                    {"status": "not_ready", "detail": f"temporal : {str(exc)[:200]}"}, status_code=503
                )
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
        port=settings.port,
        reload=settings.env == "dev",
        log_config=None,
    )
