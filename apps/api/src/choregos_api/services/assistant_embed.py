# SPDX-License-Identifier: Apache-2.0
"""Who may frame an assistant's embedded component (S63-12, ADR 0139 point 6).

The installation's `config.embed_origins` is the list of origins (`https://app.example.com`) a host
application may frame `/embed/assistants/{org}/{slug}` from. The console turns it into the page's
`frame-ancestors`; an installation without it is framed by nobody. An origin is a scheme, a host and
an optional port: no path, no wildcard (a `*` would let any page frame the assistant), and `http` only for
`localhost` (development).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AssistantInstallation, Organization
from ..errors import not_found, unprocessable
from ..origin_guard import origin_of

KEY = "embed_origins"
_LOCAL = frozenset({"localhost", "127.0.0.1"})


def _is_origin(value: object) -> bool:
    if not isinstance(value, str) or "*" in value or value != value.strip():
        return False
    parts = urlsplit(value)
    try:
        parts.port  # noqa: B018 - a malformed port raises
    except ValueError:
        return False
    if not parts.hostname or parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _LOCAL)


def check_embed_origins(config: dict[str, Any]) -> None:
    """422 when `embed_origins` is not a list of plain origins."""
    if KEY not in config:
        return
    value = config[KEY]
    bad = [o for o in value if not _is_origin(o)] if isinstance(value, list) else [value]
    if bad or not isinstance(value, list):
        raise unprocessable(
            "`config.embed_origins` must be a list of origins such as `https://app.example.com` "
            "(no path, no wildcard; `http` only for localhost)",
            [{"loc": ["config", KEY], "msg": f"not an origin: {item!r}"} for item in bad],
        )


def embed_origins(config: dict[str, Any]) -> list[str]:
    """The installation's origins, normalised and de-duplicated (an invalid entry is dropped)."""
    raw = config.get(KEY)
    found = [origin_of(o) for o in raw if _is_origin(o)] if isinstance(raw, list) else []
    return list(dict.fromkeys(found))


async def public_embed_origins(session: AsyncSession, org: str, slug: str) -> list[str]:
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    row = (
        (
            await session.execute(
                select(AssistantInstallation).where(
                    AssistantInstallation.org_id == organisation.id, AssistantInstallation.slug == slug
                )
            )
        ).scalar_one_or_none()
        if organisation is not None
        else None
    )
    if row is None:
        raise not_found("Assistant", slug)
    return embed_origins(row.config)
