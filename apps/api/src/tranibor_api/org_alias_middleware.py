# SPDX-License-Identifier: Apache-2.0
"""A former slug in a URL reaches the organisation it named (ADR 0090).

`/api/v1/orgs/<alias>/…` and `/a2a/orgs/<alias>…` are rewritten to the organisation's current slug
BEFORE routing, so every route keeps its `{org}` parameter and sees the current slug (the role checks,
the lookups and the RLS scope all compare current slugs). It is a rewrite, not a 308: a `POST` with a
body and a WebSocket upgrade go through, and no client has to follow a redirect. The response carries
`Link: <current URL>; rel="canonical"` so a client can learn the new address.

MCP addresses and qualified ids (`<org>:<project>`) are not URL segments of this shape; they go through
`resolve_project`, which resolves the organisation part the same way.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from .db.models import Organization, OrgSlugAlias
from .db.session import session_scope

#: Where an organisation's slug is a path segment of its own.
_ORG_PATH = re.compile(
    r"^(?P<head>/api/v1/orgs/|/a2a/orgs/)(?P<slug>[a-z0-9][a-z0-9-]{0,62})(?P<tail>(?:/.*)?)$"
)


async def _current_slug_of_alias(alias: str) -> str | None:
    async with session_scope(orgs="*") as session:  # `organizations` is under RLS: read as the platform
        return (
            await session.execute(
                select(Organization.slug)
                .join(OrgSlugAlias, OrgSlugAlias.org_id == Organization.id)
                .where(OrgSlugAlias.alias == alias)
            )
        ).scalar_one_or_none()


class OrgAliasMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        found = _ORG_PATH.match(scope["path"])
        current = await _current_slug_of_alias(found["slug"]) if found else None
        if found is None or current is None:
            await self.app(scope, receive, send)
            return
        path = f"{found['head']}{current}{found['tail']}"
        scope = {**scope, "path": path, "raw_path": path.encode()}

        canonical = f'<{path}>; rel="canonical"'.encode()

        async def send_with_canonical(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [*message.get("headers", []), (b"link", canonical)],
                }
            await send(message)

        await self.app(scope, receive, send_with_canonical)
