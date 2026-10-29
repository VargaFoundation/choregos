# SPDX-License-Identifier: Apache-2.0
"""Refuse a change made with the console's session cookie from another origin.

Threat: the session cookie is `SameSite=Lax`, which stops a page on another *site* but not a page on a
sibling subdomain (same site, other origin). Such a page can submit a form or a `fetch` to the API and
the browser attaches the cookie. The cookie branch of `deps.current_principal` has no Origin check.

Rule: a request that is not a safe method, carries the session cookie and no `Authorization: Bearer`
header must come from the console's origin (or a listed CORS origin). A browser always sends `Origin` on
such a request, or `Sec-Fetch-Site` when it omits it (an empty `Origin` counts as omitted); an `Origin`
of `null` is refused. Origins are compared normalised (lower-case, default port dropped). A request with
neither header (a CLI) passes, and so does every request without the session cookie (webhooks, bearer
tokens). Any other `Authorization` scheme does not exempt: the cookie still authenticates it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .errors import PROBLEM_JSON

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_FOREIGN_FETCH_SITES = frozenset({"cross-site", "same-site"})


def origin_of(url: str) -> str:
    """`scheme://host[:port]`, lower-cased, without the scheme's default port (`:443` https, `:80` http)."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme  # urlsplit lower-cases the scheme and `hostname`
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None or (scheme, port) in {("https", 443), ("http", 80)}:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def allowed_origins(settings: Settings) -> set[str]:
    """The console's, the API's and every `CHOREGOS_CORS_ORIGINS` origin."""
    return {origin_of(settings.public_url), origin_of(settings.api_url), *map(origin_of, settings.cors_list)}


def origin_allowed(origin: str | None, settings: Settings) -> bool:
    """No `Origin` (a CLI, a server) passes; a browser's must be the console's, the API's, or CORS's."""
    if not origin:
        return True
    return origin_of(origin) in allowed_origins(settings)


def check_cors_origins(settings: Settings) -> None:
    if "*" in settings.cors_list:
        raise RuntimeError(
            "CHOREGOS_CORS_ORIGINS lists `*`: with credentials, any origin would be reflected; "
            "list the console origin(s)"
        )


def install_origin_guard(app: FastAPI) -> None:
    @app.middleware("http")
    async def origin_guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        settings = get_settings()
        # Only a bearer token exempts the request: `deps.current_principal` falls back to the cookie for
        # any other `Authorization` value.
        bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
        if request.method not in SAFE_METHODS and settings.session_cookie in request.cookies and not bearer:
            origin = request.headers.get("origin", "").strip()
            if origin:
                refused = origin == "null" or not origin_allowed(origin, settings)
            else:
                refused = request.headers.get("sec-fetch-site", "").lower() in _FOREIGN_FETCH_SITES
            if refused:
                return JSONResponse(
                    {
                        "type": "about:blank",
                        "title": "Forbidden",
                        "status": status.HTTP_403_FORBIDDEN,
                        "detail": "cross-origin request refused: a change made with the session cookie "
                        "must come from the console origin",
                        "instance": request.url.path,
                    },
                    status_code=status.HTTP_403_FORBIDDEN,
                    media_type=PROBLEM_JSON,
                )
        return await call_next(request)
