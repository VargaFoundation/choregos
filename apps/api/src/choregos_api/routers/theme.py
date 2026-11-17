# SPDX-License-Identifier: Apache-2.0
"""`GET /theme`: the look of the console, as data (S39-03, ADR 0064).

Public, because the sign-in page is drawn before anyone is signed in. What it serves is a
validated theme (`choregos_api.theme`): the community default, or what a plugin's provider returned
if it passed the contract and the console's contrast budget.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from ..db.session import ALL_ORGS, get_sessionmaker, limit_to_organisations
from ..extensions import theme_provider
from ..tenant_host import has_host_resolver, resolve_host
from ..theme import DEFAULT_THEME, ORG_SLUG, resolve_theme

router = APIRouter(tags=["session"])


@router.get("/theme", operation_id="theme", response_model=None)
async def get_theme(
    request: Request,
    org: Annotated[str | None, Query()] = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """The look of the console, for the host it is reached on or for `?org=`.

    On a host an organisation owns (ADR 0175, `declare_host_resolver`), that organisation's theme
    is served whatever `?org=` says: a host's look is the host's, not the visitor's remembered
    choice. The host is `X-Forwarded-Host` (first entry) or `Host`; the console's server, which
    calls this route from inside the cluster, forwards the visitor's host in that header. Both
    headers are client-controlled, which is harmless here: the route is public, and a host only
    selects a look the route already serves for `?org=`. `X-Choregos-Host-Org` names the
    organisation the host belongs to, so the console can imply it on its sign-in page.
    """
    if org is not None and not ORG_SLUG.match(org):
        org = None  # an unknown slug is "no organisation", never an error on a public route
    host_org: str | None = None
    if theme_provider() is None and not has_host_resolver():
        theme, source = DEFAULT_THEME, "default"
    else:
        # The providers read their own tables, for any organisation, before anyone is signed in:
        # they get the platform scope, like the webhooks (`get_platform_db`).
        async with get_sessionmaker()() as session:
            await limit_to_organisations(session, ALL_ORGS)
            host = request.headers.get("x-forwarded-host") or request.headers.get("host")
            host_org = await resolve_host(session, host)
            theme, source = await resolve_theme(session, host_org or org)
            await session.commit()
    body = json.dumps(theme, sort_keys=True, separators=(",", ":"))
    etag = f'"{hashlib.sha256(body.encode()).hexdigest()[:32]}"'
    headers = {
        "ETag": etag,
        "Cache-Control": "no-cache",
        "Vary": "Host, X-Forwarded-Host",
        "X-Choregos-Theme": source,
    }
    if host_org:
        headers["X-Choregos-Host-Org"] = host_org
    if if_none_match == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(theme, headers=headers)
