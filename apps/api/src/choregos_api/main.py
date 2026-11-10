# SPDX-License-Identifier: Apache-2.0
"""The Choregos FastAPI application."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from choregos_adapters import load_extensions
from choregos_core import telemetry
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .a2a.push import push_loop
from .adapter_wiring import attach_lexical_memory
from .bootstrap import bootstrap_installation
from .config import get_settings
from .db.session import create_all, dispose_engine
from .errors import REQUEST_ID_PATTERN, install_error_handlers, internal_error
from .events import PostgresRelay
from .knowledge.conversion import conversions
from .logging import bind, clear, configure_logging, get_logger
from .metrics import expose, http_requests, refresh_loop
from .origin_guard import check_cors_origins, install_origin_guard
from .routers import (
    actions,
    admin,
    agent_realtime,
    agent_releases,
    agent_session_pods,
    agent_sessions,
    agents,
    assistant_conversations,
    assistant_knowledge,
    assistant_memory,
    assistants,
    auth,
    budget_alerts,
    connector_catalogue,
    connectors,
    conversations,
    costs,
    egress,
    findings,
    github_app,
    integrations,
    internal,
    knowledge,
    machine_accounts,
    mcp_catalogue,
    mcp_gateway,
    memories,
    memory,
    model_catalogue,
    models,
    my_sessions,
    org_connectors,
    outbound_proxy,
    platform_identity,
    plugin_sources,
    plugins,
    procedures,
    projects,
    readiness,
    received_events,
    remote_credentials,
    runs,
    skill_catalogue,
    skills,
    templates,
    theme,
    tracker_identities,
    trains,
    triggers,
    ui,
    webhooks,
    workflow_catalogue,
    workflows,
    workitems,
)
from .security import check_session_secret
from .services.action_relay import relay_loop
from .services.plugin_defaults import check_configuration, reconcile_in_background
from .services.plugin_supply import warm_shipping
from .services.skill_scanner import mirror_loop

API_PREFIX = "/api/v1"
logger = get_logger("choregos.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    telemetry.configure_tracing("choregos-api")
    logger.info("API starting", env=settings.env, fakes=settings.fakes)
    if not settings.is_sqlite:
        await _refuse_the_superuser()
    _warn_without_secrets_key(settings.fakes)
    metrics_refresh = asyncio.create_task(refresh_loop(settings.metrics_refresh_seconds))
    relay = None if settings.is_sqlite else PostgresRelay(settings.database_url)
    if relay is not None:
        relay.start()
    if not settings.is_sqlite:
        await bootstrap_installation(settings)
    if settings.is_sqlite:
        # SQLite only: a file or memory database, thrown away with the process, that no migration
        # follows. Everywhere else the schema comes from Alembic, `dev` included. The opposite
        # created the tables when the API started, and the next migration ran into
        # `relation "agent_backends" already exists`: the deployment failed blaming the
        # migrations, when it was the API that had taken their place.
        await create_all()
        await bootstrap_installation(settings)
    # Conversions a stopped replica left behind (ADR 0051): pending ones, and stale `converting`.
    await conversions.recover()
    # Approved actions whose start after the commit was lost (ADR 0085), once the schema exists.
    action_relay = asyncio.create_task(relay_loop())
    # A2A push notifications (ADR 0078 §5): read from the persisted events, at least once.
    a2a_push = asyncio.create_task(push_loop())
    # The deployment's default plugins in every organisation (ADR 0093 §5), once the schema and the
    # bootstrap organisation exist. In the background: a slow source never holds the readiness probe.
    plugin_defaults = asyncio.create_task(reconcile_in_background(settings))
    # The skill scanner's local OSV mirror (ADR 0095), rebuilt in each replica.
    osv_mirror = asyncio.create_task(mirror_loop(settings))
    # The plugin sources' index, read once now: the first listing of the library does not wait for it.
    shipping_warmup = asyncio.create_task(warm_shipping(settings))
    yield
    # A conversion cut here stays `converting`; the next start-up takes it back once stale.
    await conversions.stop()
    if relay is not None:
        await relay.stop()
    for task in (metrics_refresh, action_relay, a2a_push, plugin_defaults, osv_mirror, shipping_warmup):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await dispose_engine()
    telemetry.shutdown_tracing()
    logger.info("API stopping")


def _warn_without_secrets_key(fakes: bool) -> None:
    """Connector secrets typed in the console need a key from the deployment (ADR 0058). Without
    one the API still runs — references (`env:`) keep working — but a write of a value answers 409:
    say it once, at start-up, rather than at the first administrator who tries."""
    from .secret_store import ENV_KEKS, keyring_from_env

    if fakes:
        return
    if keyring_from_env() is None:
        logger.warning(
            "secrets.key_missing", detail=f"{ENV_KEKS} is not set: connector secrets cannot be typed"
        )


async def _refuse_the_superuser() -> None:
    """A PostgreSQL superuser ignores RLS: with it, the isolation of the organisations does not
    exist, whatever the policies say. We say so at startup, loudly, and we refuse outside
    development, because a multi-tenant platform without isolation lies."""
    from sqlalchemy import text

    from .db.session import get_engine

    async with get_engine().connect() as conn:
        superuser = (
            await conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
        ).scalar()
    if superuser:
        message = (
            "the API connects to PostgreSQL as a SUPERUSER: RLS does not apply, "
            "the organisations are not isolated. Use an application role without SUPERUSER."
        )
        if get_settings().env in {"staging", "prod"}:
            raise RuntimeError(message)
        logger.error(message)


def _check_the_edition(settings: Any) -> None:
    """What the deployment announces must be what is loaded (ADR 0024).

    The edition that runs is not a chart value: it is the enterprise extension that declares it
    when it loads. Nothing linked the two. An operator who writes `global.edition: enterprise` on
    community images thus got a platform that starts, looks healthy, and refuses the second
    organisation, the symptom arriving weeks after the cause, on an action with no apparent
    relation.

    The refusal is at startup and it names what is missing. The reverse is not refused: a
    deployment that announces nothing and loads the enterprise extension is simply a deployment
    that forgot to say so, and denying it startup would help nobody; the `choregos_edition_info`
    metric and `GET /edition` tell the truth in both cases.
    """
    from .edition import ENTERPRISE, current

    if settings.edition == ENTERPRISE and current() != ENTERPRISE:
        raise RuntimeError(
            "global.edition=enterprise but no enterprise edition extension declared itself. "
            "The image is the community edition's, or the `choregos-ee` package is not "
            "installed, or its `choregos.plugins` entry point does not call `declare`. "
            "Fix the image, or set global.edition=community again."
        )


def _include_extension_routers(app: FastAPI, core_routers: tuple[APIRouter, ...]) -> set[tuple[str, str]]:
    """The routers declared by the extensions (`extensions.declare_router`), AFTER those of the core.

    An overlap with a route of the core stops the startup: Starlette would serve one and ignore the
    other without a word (see `extensions.py`).

    What the core serves is read from ITS routers, not from `app.routes`: since FastAPI 0.141, an
    included router appears there as an opaque `_IncludedRouter`, and the first version of this
    guard, which walked `app.routes`, saw no overlap.
    """
    from .extensions import declared_routers, signatures

    served: set[tuple[str, str]] = set()
    for core_router in core_routers:
        served |= signatures(core_router, API_PREFIX)
    for router in declared_routers():
        overlapping = signatures(router, API_PREFIX) & served
        if overlapping:
            listed = ", ".join(f"{m} {p}" for m, p in sorted(overlapping))
            # A test matches these words, and the message stays French with them.
            raise RuntimeError(
                f"un greffon déclare des routes déjà servies : {listed}. "  # lang: fr-ok
                "Un greffon AJOUTE des routes ; "  # lang: fr-ok
                "remplacer celles du cœur se demande par une couture nommée."  # lang: fr-ok
            )
        app.include_router(router, prefix=API_PREFIX)
        served |= signatures(router, API_PREFIX)
    return served


def _check_the_sections(served: set[tuple[str, str]]) -> None:
    """The administration sections of the extensions (ADR 0032): an invalid manifest, an unknown
    permission, or a path that no route serves stops the startup: a section that leads nowhere is
    a broken screen, discovered by an administrator at the worst moment.

    The refusal messages stay French: the tests of the sections match them.
    """
    import re

    import choregos_contracts as contracts
    from jsonschema import Draft202012Validator

    from .extensions import declared_sections, section_paths
    from .rbac import Permission

    def shape(path: str) -> str:
        return re.sub(r"\{[^}]*\}", "{}", path)

    validator = Draft202012Validator(contracts.load_schema("ui-manifest.schema.json"))
    known = {(method, shape(path)) for method, path in served}
    for section in declared_sections():
        name = section.get("id", "?")
        errors = sorted(validator.iter_errors(section), key=lambda e: list(e.path))
        if errors:
            detail = "; ".join(f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in errors)
            raise RuntimeError(f"section d'administration `{name}` invalide : {detail}")  # lang: fr-ok
        known_permissions = {p.value for p in Permission}
        if section["permission"] not in known_permissions:
            raise RuntimeError(
                f"section `{name}` : permission inconnue `{section['permission']}`"  # lang: fr-ok
            )
        for block in section["blocks"]:
            if "permission" in block and block["permission"] not in known_permissions:
                raise RuntimeError(f"section `{name}` : unknown block permission `{block['permission']}`")
        for method, path in section_paths(section):
            if (method, shape(API_PREFIX + path)) not in known:
                raise RuntimeError(
                    f"section `{name}` : `{method} {path}` n'est servi par aucune route"  # lang: fr-ok
                )


def create_app() -> FastAPI:
    settings = get_settings()
    attach_lexical_memory()
    # The extensions installed alongside (enterprise edition, house connectors) register
    # themselves. A declared extension that does not load stops the startup, on purpose.
    load_extensions()
    _check_the_edition(settings)
    # The API signs the sessions, so it alone refuses a session secret that cannot sign safely: the
    # orchestrator and the migrations Job build the same Settings without it (see `check_session_secret`).
    check_session_secret(settings)
    # The plugin sources and default plugins (ADR 0093): an unreadable configuration stops the API here,
    # naming the setting, rather than answering 500 at the first install.
    check_configuration(settings)
    app = FastAPI(
        title="Choregos API",
        # The package's version, not a constant: the API announced itself as `1.0.0` from the first
        # day, and the dev tenant's `/openapi.json` repeated it while the chart and the packages
        # were at 0.4.1. A version that lies is what this repository hunts.
        version=__version__,
        description="A work item comes in, a controlled production release comes out.",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    check_cors_origins(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        # Every request header apps/web/src/lib/api.ts sets (only Content-Type), plus the credentials
        # and tracing ones; never `*`.
        allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )
    install_error_handlers(app)
    from .org_alias_middleware import OrgAliasMiddleware

    # A former slug is replaced before anything routes (ADR 0090); the log keeps the path as it was sent.
    app.add_middleware(OrgAliasMiddleware)
    from .rate_limit import install as install_the_rate_limiter

    install_the_rate_limiter(app, settings.rate_limit_per_minute)
    # After the limiter and before the logging middleware: logging stays outermost, so a refusal is
    # logged with its request id.
    install_origin_guard(app)

    @app.middleware("http")
    async def logging_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        clear()
        # One identifier per request, taken from the input if there is one (ingress, client),
        # returned in the response: it is what links an API log to a front-end log.
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        bind(path=request.url.path, method=request.method, request_id=request_id)
        try:
            response = await call_next(request)
        except Exception:
            # Logged here, with the request id; answered as problem+json carrying the same id
            # (S26-02) — Starlette answered a plain-text "Internal Server Error".
            logger.exception("unhandled error", path=request.url.path)
            response = internal_error(request, request_id)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        response.headers["X-Request-Id"] = request_id
        route = getattr(request.scope.get("route"), "path", request.url.path)
        http_requests.labels(request.method, route, str(response.status_code)).inc()
        if not request.url.path.startswith(("/healthz", "/readyz")):
            logger.info("request", status=response.status_code, duration_ms=duration_ms)
        return response

    core_routers = (
        auth.router,
        my_sessions.router,
        ui.router,
        theme.router,
        integrations.router,
        procedures.router,
        platform_identity.router,
        projects.router,
        readiness.router,
        connectors.router,
        org_connectors.router,
        mcp_catalogue.router,
        connector_catalogue.router,
        remote_credentials.router,
        github_app.router,
        actions.router,
        workflows.router,
        workflow_catalogue.router,
        models.router,
        model_catalogue.router,
        workitems.router,
        runs.router,
        trains.router,
        findings.router,
        memory.router,
        memories.router,
        costs.router,
        budget_alerts.router,
        egress.router,
        outbound_proxy.router,
        mcp_gateway.router,
        templates.router,
        admin.router,
        tracker_identities.router,
        agents.router,
        agent_releases.router,
        agent_sessions.router,
        conversations.router,
        assistant_conversations.router,
        agent_realtime.router,
        agent_session_pods.router,
        skills.router,
        skill_catalogue.router,
        assistants.router,
        assistant_memory.router,
        assistant_knowledge.router,
        plugins.router,
        plugin_sources.router,
        knowledge.router,
        webhooks.router,
        received_events.router,
        machine_accounts.router,
        triggers.router,
        internal.router,
    )
    for router in core_routers:
        app.include_router(router, prefix=API_PREFIX)
    _check_the_sections(_include_extension_routers(app, core_routers))

    # The MCP door for external clients (ADR 0030): at the root, as R-SOC-MCP-01 requires, outside
    # the OpenAPI schema: MCP is its own protocol, and its tools are tested where they are defined.
    from .mcp import router as mcp_door

    app.include_router(mcp_door)
    # The A2A door (ADR 0078), beside it and out of the OpenAPI document for the same reason.
    from .a2a import router as a2a_door

    app.include_router(a2a_door)

    @app.get("/metrics", tags=["session"], operation_id="metrics", include_in_schema=False)
    async def metrics() -> Response:
        """The series that the dashboards and the alerts read (see `metrics.py`)."""
        return Response(content=expose(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/healthz", tags=["session"], operation_id="healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    def _build_revision() -> str | None:
        """The source revision the image was built from (`CHOREGOS_BUILD_REVISION`, set by the build).

        Public like the rest of `/edition`. Anything that is not a hex commit id is dropped, so a
        misconfigured variable cannot put free text in a public response.
        """
        value = os.environ.get("CHOREGOS_BUILD_REVISION", "").strip()
        return value if re.fullmatch(r"[0-9a-f]{7,40}", value) else None

    async def running_edition() -> dict[str, Any]:
        """Which edition runs, and what it allows itself (ADR 0024).

        Without a route, the only way to know what one is connected to would be to attempt an action
        and read the refusal. An operator must be able to ask.
        """
        from .edition import CORE_LICENCE, current, edition_licence, edition_version, features, publisher

        return {
            "edition": current(),
            "features": sorted(features()),
            "version": __version__,
            "publisher": publisher(),
            "edition_version": edition_version(),
            "licence": CORE_LICENCE,
            "edition_licence": edition_licence(),
            "build": {"revision": _build_revision()},
        }

    # The contract places it under `/api/v1` (`servers: /api/v1`): that is where the console asks
    # for it, since Next relays only that prefix. It used to be served only at the root, which the
    # Ingress sends to the console: nobody outside the cluster could read it. The root stays
    # served, outside the schema, for the probes and the operating docs that cite it.
    app.add_api_route(
        f"{API_PREFIX}/edition", running_edition, methods=["GET"], tags=["session"], operation_id="edition"
    )
    app.add_api_route("/edition", running_edition, methods=["GET"], include_in_schema=False)

    @app.get("/readyz", tags=["session"], operation_id="readyz")
    async def readyz() -> Any:
        """Ready = the database AND Temporal answer. Otherwise 503: the pod takes no traffic.

        Temporal was not checked: a pod whose Temporal was unreachable stayed in the service and
        answered 500 on every decision, every train, every webhook.
        """
        from sqlalchemy import text

        from .db.session import get_engine
        from .temporal import RealTemporal, get_temporal

        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            return JSONResponse(
                {"status": "not_ready", "detail": f"database: {str(exc)[:200]}"}, status_code=503
            )
        temporal = get_temporal()
        if isinstance(temporal, RealTemporal):
            try:
                client = await asyncio.wait_for(temporal.client(), timeout=2.0)
                if not await asyncio.wait_for(client.service_client.check_health(), timeout=2.0):
                    raise RuntimeError("check_health answered no")
            except Exception as exc:
                return JSONResponse(
                    {"status": "not_ready", "detail": f"temporal: {str(exc)[:200]}"}, status_code=503
                )
        return {"status": "ready"}

    return app


app = create_app()


def main() -> None:
    """Entry point `choregos-api`."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "choregos_api.main:app",
        host="0.0.0.0",  # noqa: S104 - container
        port=settings.port,
        reload=settings.env == "dev",
        log_config=None,
    )
