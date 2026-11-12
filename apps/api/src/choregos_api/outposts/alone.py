# SPDX-License-Identifier: Apache-2.0
"""The outposts' door served alone: `python -m choregos_api.outposts` (ADR 0148 §8).

The API serves the door as one more router. Polls wait by the hundred, each for 25 seconds: where a
deployment splits the door out, this is what its Deployment runs, from the API image, so that those
requests take no worker and no pooled connection from the console and the runs
(`CHOREGOS_DB_POOL_SIZE`, `CHOREGOS_DB_MAX_OVERFLOW`: this process's own pool).

What it holds, and nothing else:

- the door's four verbs (`door.router`), under the same checks at start-up as the API's (`check_the_door`);
- two probes, `/healthz` and `/readyz` (the database answers): they are this process's, not the
  door's, and no route outside the cluster leads to them;
- the wake-up channel of the relay's `LISTEN` connection, and that channel only: no platform event is
  heard here, since nobody in this process would be woken by one;
- the extensions an edition installs, because the key wrapper a call's arguments wait under may be
  one of theirs (ADR 0103).

What it leaves to the API, on purpose. **The sweep** (`rendezvous.sweep_loop`) closes what ran out of
time at outposts nobody polls or calls any more. It lists every outpost under the platform's scope,
which the door never takes (ADR 0148 §6), and the API runs it whether or not the door is split out:
one place in both layouts, and none here. A poll still closes what ran out of time at its own
outpost at its first look, as it does in the API. No other route of the API is imported, let alone
served: this module is part of the door's package, whose import closure is refused whatever reaches
an organisation's connectors (ADR 0148 §12).

Stopping. On `SIGTERM` the server accepts nothing more and lets the polls it holds end by
themselves: each answers within the wait it asked for, and gives its slot back. `GRACE_S` is the
longest that can take; past it what is left is cancelled, and a cancelled poll gives its slot back
too (`door._poll`). Whatever runs this process gives it longer than `GRACE_S` to stop.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Final

from choregos_adapters import load_extensions
from choregos_contracts.outpost import MAX_WAIT_S
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .. import __version__
from ..config import Settings, get_settings
from ..db.session import dispose_engine, get_engine
from ..errors import REQUEST_ID_PATTERN, install_error_handlers, internal_error
from ..events import PostgresRelay
from ..logging import bind, clear, configure_logging, get_logger
from . import rendezvous
from .auth import check_the_door
from .door import SLOT_MARGIN_S, router

logger = get_logger("choregos.outposts.alone")

#: What `uvicorn` imports and calls: an application built when the process starts, never at import.
APPLICATION: Final = "choregos_api.outposts.alone:create_app"
#: How long a stopping process lets the polls it holds end by themselves: the longest wait a poll may
#: ask for, and the margin its slot lives on.
GRACE_S: Final = MAX_WAIT_S + SLOT_MARGIN_S
PROBES: Final = ("/healthz", "/readyz")


async def _refuse_the_superuser(settings: Settings) -> None:
    """The door's isolation is row-level security and nothing else (ADR 0148 §6), and a PostgreSQL
    superuser ignores it. The same rule as the API's start-up: said loudly everywhere, refused in
    staging and prod."""
    async with get_engine().connect() as connection:
        superuser = (
            await connection.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
        ).scalar()
    if not superuser:
        return
    message = (
        "the outposts' door connects to PostgreSQL as a SUPERUSER: row-level security does not apply, "
        "one outpost is not kept from another's rows. Use an application role without SUPERUSER."
    )
    if settings.env in {"staging", "prod"}:
        raise RuntimeError(message)
    logger.error(message)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    logger.info("the outposts' door starts, served alone", env=settings.env)
    relay: PostgresRelay | None = None
    if not settings.is_sqlite:
        await _refuse_the_superuser(settings)
        # The wake-ups, and nothing else of what the platform announces (ADR 0148 §7). On SQLite
        # there is no relay: a poll looks again periodically, which serves a local bench.
        relay = PostgresRelay(settings.database_url, events=False)
        relay.listen_also(rendezvous.CHANNEL, rendezvous.woken)
        relay.start()
    try:
        yield
    finally:
        if relay is not None:
            await relay.stop()
        await dispose_engine()
        logger.info("the outposts' door stops")


async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def readyz() -> Response:
    """Ready when the database answers: every verb of the door reads a row before anything else."""
    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as error:
        return JSONResponse({"status": "not_ready", "detail": f"database: {str(error)[:200]}"}, 503)
    return JSONResponse({"status": "ready"})


def create_app() -> FastAPI:
    """The application that serves the door and nothing else. It refuses to be built where there is
    no door to serve: outposts switched off, or a door without its origin or its signing key."""
    settings = get_settings()
    if not settings.outposts:
        raise RuntimeError(
            "CHOREGOS_OUTPOSTS is off: a process that serves the outposts' door alone has nothing to "
            "serve. Switch outposts on, or do not run it."
        )
    # An edition's key wrapper registers itself here, as it does in the API (ADR 0103): without it
    # this process could not open the arguments a call waits under. A declared extension that does
    # not load stops the start-up.
    load_extensions()
    check_the_door(settings)
    # No document, and so no explorer of one: the door's contract is `packages/contracts/outpost-door.yaml`.
    app = FastAPI(
        title="Choregos, the outposts' door", version=__version__, lifespan=lifespan, openapi_url=None
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def logged(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """One log line per request, under the id that ties it to the answer, as the API writes them."""
        start = time.perf_counter()
        clear()
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        bind(path=request.url.path, method=request.method, request_id=request_id)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled error", path=request.url.path)
            response = internal_error(request, request_id)
        response.headers["X-Request-Id"] = request_id
        if request.url.path not in PROBES:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info("request", status=response.status_code, duration_ms=duration_ms)
        return response

    app.include_router(router)
    app.add_api_route("/healthz", healthz, methods=["GET"], include_in_schema=False)
    app.add_api_route("/readyz", readyz, methods=["GET"], include_in_schema=False)
    return app


def main() -> None:
    """Serve the door until told to stop, then let the polls in hand end (`GRACE_S`)."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        APPLICATION,
        factory=True,
        host="0.0.0.0",  # noqa: S104 - a container
        port=settings.port,
        log_config=None,
        timeout_graceful_shutdown=GRACE_S,
    )


__all__ = ["APPLICATION", "GRACE_S", "PROBES", "create_app", "lifespan", "main"]
