# SPDX-License-Identifier: Apache-2.0
"""The run's API answers only where runs are (ADR 0144).

`/api/v1/internal/*` is what a run's pod, a hosted session's pod and the platform's own proxies call,
each with a token of its own. A deployment routes the whole `/api` prefix of its public hostnames to
this process, so those routes answered there as well: a token that left its pod could be presented
from anywhere. Runs do not come that way: they call the address the workers give them
(`CHOREGOS_INTERNAL_API_URL`), which is the in-cluster Service in the chart and the service name in
the compose install.

Rule: a request under `/api/v1/internal` that arrives under a public hostname of this API is answered
404, as an unknown route is, unless that hostname is a run door (`CHOREGOS_RUN_DOOR_HOSTS`). Any
other name (the Service, a compose service, `localhost`, an address) is served as before.

- **Public** is what the deployment declares: the hosts of `CHOREGOS_PUBLIC_URL`, `CHOREGOS_API_URL`
  and `CHOREGOS_CORS_ORIGINS`, and `CHOREGOS_PUBLIC_HOSTS` (the chart's ingress hostnames). Ports are
  not compared; a leading `*.` names every sub-domain.
- **Arrives under** is the `Host` header and every `X-Forwarded-Host` entry. The second is there for
  a reverse proxy that rewrites `Host`: the console's own relay of `/api/v1` does, and says where
  the request came in with that header. A client can write the header too, which only gets its own
  request refused: a name in it never opens anything, the run door included.
- **Never the address runs are sent to.** When the host of `CHOREGOS_INTERNAL_API_URL` (or of
  `CHOREGOS_API_URL` without it) is itself a public one and no run door covers it, `enforce` would
  end every run at its first call. The guard then only warns, and `announce` says once at start-up
  what to configure.

`CHOREGOS_INTERNAL_API_GUARD` is `off`, `warn` (the default: log `internal_api.would_refuse`, serve)
or `enforce`. A log line carries the host that matched, the header it was read from and the first path
segment after `/internal`: never a token, another header or the query string.

What it is not: a wall against a caller inside the cluster, which reaches the Service by its name
(the tokens and the network policies are), nor against a hostname routed to this process that the
deployment never declared.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from .config import Settings, get_settings
from .logging import get_logger

#: Everything a run, a session pod or a platform proxy calls with its own token.
PREFIX = "/api/v1/internal"
#: The longest host a log line carries: a DNS name's limit.
MAX_HOST_CHARS = 253

logger = get_logger("choregos.internal_api")


def hostname(value: str) -> str:
    """The name in a `Host` value or a setting's entry: lower-case, without port, brackets or final dot."""
    host = value.strip().lower()
    if host.startswith("["):  # an IPv6 literal, with or without a port
        host = host[1:].partition("]")[0]
    elif host.count(":") == 1:
        host = host.partition(":")[0]
    return host.rstrip(".")


def _url_host(url: str) -> str:
    return (urlsplit(url.strip()).hostname or "").rstrip(".")


def _names(host: str, patterns: tuple[str, ...]) -> bool:
    return any(
        host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:])) for pattern in patterns
    )


@dataclass(frozen=True, slots=True)
class Guard:
    """What one configuration decides, computed once."""

    mode: str
    public: tuple[str, ...]
    doors: tuple[str, ...]
    #: The host runs are told to call back.
    callback: str

    def public_host(self, presented: Iterable[tuple[str, str]]) -> tuple[str, str] | None:
        """The first `(header, host)` that names a public host which is not a run door."""
        for header, host in presented:
            if host and _names(host, self.public) and not _names(host, self.doors):
                return header, host
        return None

    @property
    def held(self) -> bool:
        """Runs call back on a public host that is no run door: refusing there would end them all."""
        return self.public_host((("callback", self.callback),)) is not None

    @property
    def refuses(self) -> bool:
        return self.mode == "enforce" and not self.held


@lru_cache(maxsize=16)
def _guard(
    mode: str,
    public_url: str,
    api_url: str,
    cors_origins: tuple[str, ...],
    public_hosts: tuple[str, ...],
    run_door_hosts: tuple[str, ...],
    internal_api_url: str,
) -> Guard:
    declared = [_url_host(url) for url in (public_url, api_url, *cors_origins)]
    declared += [hostname(entry) for entry in public_hosts]
    return Guard(
        mode=mode,
        public=tuple(dict.fromkeys(host for host in declared if host)),
        doors=tuple(dict.fromkeys(hostname(entry) for entry in run_door_hosts)),
        callback=_url_host(internal_api_url or api_url),
    )


def guard_of(settings: Settings) -> Guard:
    return _guard(
        settings.internal_api_guard,
        settings.public_url,
        settings.api_url,
        tuple(settings.cors_list),
        tuple(settings.public_hosts),
        tuple(settings.run_door_hosts),
        settings.internal_api_url,
    )


def presented_hosts(headers: Headers) -> list[tuple[str, str]]:
    """`(header, host)` for the name the request arrived under and each one a proxy says it did.

    Every line of each header is read: a second `Host` line, or a second `X-Forwarded-Host` one, is
    one more name the request may have been routed by.
    """
    found = [("host", hostname(line)) for line in headers.getlist("host")]
    for line in headers.getlist("x-forwarded-host"):
        found += [("x-forwarded-host", hostname(entry)) for entry in line.split(",")]
    return [(header, host) for header, host in found if host]


def _guarded(path: str) -> bool:
    return path == PREFIX or path.startswith(f"{PREFIX}/")


def _family(path: str) -> str:
    """`/api/v1/internal/runs`: who was calling (a run, a session pod, a proxy), without any id."""
    return "/".join(path.split("/")[:5])


def announce(settings: Settings) -> None:
    """Say once, at start-up, what the guard does, and what to configure when it cannot refuse."""
    guard = guard_of(settings)
    logger.info(
        "internal_api.guard",
        mode=guard.mode,
        public_hosts=list(guard.public),
        run_door_hosts=list(guard.doors),
        callback_host=guard.callback,
    )
    if guard.mode != "off" and guard.held:
        logger.warning(
            "internal_api.guard_not_enforced",
            mode=guard.mode,
            callback_host=guard.callback,
            detail=(
                f"runs call back on `{guard.callback}`, a public hostname of this API that is not a run "
                f"door: {PREFIX} stays served there and `enforce` is not applied, so that no run is "
                "refused. Send runs to the in-cluster Service, or give them a hostname of their own "
                "(`choregos-api.runDoor.hostnames` in the chart, CHOREGOS_RUN_DOOR_HOSTS) and set "
                "CHOREGOS_INTERNAL_API_URL (`global.internalApiUrl`) to it."
            ),
        )


class InternalApiGuard:
    """One guard for every route under `PREFIX`, whatever router declares it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or not _guarded(scope["path"]):
            await self.app(scope, receive, send)
            return
        guard = guard_of(get_settings())
        found = None if guard.mode == "off" else guard.public_host(presented_hosts(Headers(scope=scope)))
        if found is None:
            await self.app(scope, receive, send)
            return
        header, host = found
        seen = {"host": host[:MAX_HOST_CHARS], "header": header, "path_prefix": _family(scope["path"])}
        if not guard.refuses:
            logger.warning("internal_api.would_refuse", mode=guard.mode, **seen)
            await self.app(scope, receive, send)
            return
        logger.warning("internal_api.refused", **seen)
        if scope["type"] == "websocket":
            await WebSocketClose()(scope, receive, send)
            return
        # What the router answers for a path it does not know, byte for byte.
        await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)


def install_internal_api_guard(app: FastAPI) -> None:
    app.add_middleware(InternalApiGuard)
