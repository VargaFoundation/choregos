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

from fastapi import APIRouter, Header, Query, Response
from fastapi.responses import JSONResponse

from ..db.session import TOUT, get_sessionmaker, limiter_aux_organisations
from ..greffons import theme_provider
from ..theme import DEFAULT_THEME, ORG_SLUG, resolve_theme

router = APIRouter(tags=["session"])


@router.get("/theme", operation_id="theme", response_model=None)
async def get_theme(
    org: Annotated[str | None, Query()] = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    if org is not None and not ORG_SLUG.match(org):
        org = None  # an unknown slug is "no organisation", never an error on a public route
    if theme_provider() is None:
        theme, source = DEFAULT_THEME, "default"
    else:
        # The provider reads its own tables, for any organisation, before anyone is signed in:
        # it gets the platform scope, like the webhooks (`get_db_plateforme`).
        async with get_sessionmaker()() as session:
            await limiter_aux_organisations(session, TOUT)
            theme, source = await resolve_theme(session, org)
            await session.commit()
    body = json.dumps(theme, sort_keys=True, separators=(",", ":"))
    etag = f'"{hashlib.sha256(body.encode()).hexdigest()[:32]}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache", "X-Choregos-Theme": source}
    if if_none_match == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(theme, headers=headers)
