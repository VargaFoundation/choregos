# SPDX-License-Identifier: Apache-2.0
"""The installation owners' page of an `installation` memory space (ADR 0136, S63-09).

What a conversation proposes in a space shared by everyone of the assistant (`owner_only`, or
`agent_proposes`) waits, `proposed`, for the installation's owners: they confirm or reject it here, and may
write it themselves (`add`). Only `installation` spaces are on this page: a person's own memory is theirs.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import MemoryStore
from ..errors import conflict, not_found, unprocessable
from ..schemas.assistant_memory import MemoryAction, MemorySpace
from . import assistant_memory as memory
from . import assistant_memory_page as page


def _decl(spaces: memory.Spaces, space: str) -> dict[str, Any]:
    found = spaces.decls.get(space)
    if found is None or found.get("scope") != "installation":
        raise not_found("Installation memory space", space)
    return found


async def _stores(session: AsyncSession, spaces: memory.Spaces, space: str) -> list[MemoryStore]:
    rows = await session.execute(
        select(MemoryStore).where(
            MemoryStore.installation_id == spaces.installation.id,
            MemoryStore.user_id.is_(None),
            MemoryStore.slug == space,
        )
    )
    return list(rows.scalars())


async def view(session: AsyncSession, spaces: memory.Spaces, space: str) -> MemorySpace:
    return await page.space_of(session, _decl(spaces, space), space, await _stores(session, spaces, space))


async def act(session: AsyncSession, spaces: memory.Spaces, space: str, body: MemoryAction) -> MemorySpace:
    decl = _decl(spaces, space)
    if body.action == "add":
        await page.add(session, spaces, decl, None, body)
    elif body.action in ("confirm", "reject"):
        stores = await _stores(session, spaces, space)
        if not stores:
            raise not_found("Memory", space)
        if body.version is not None:
            await page.decide_profile(session, stores, body)
        else:
            fact = await page.fact_of(session, stores, body.fact_id)
            if fact.status != "proposed":
                raise conflict(f"the fact is {fact.status}: only a proposal is confirmed or rejected")
            fact.status = "active" if body.action == "confirm" else "rejected"
    else:
        raise unprocessable("the owners confirm, reject or add: `pin` and `correct` are a person's own")
    await session.flush()
    return await view(session, spaces, space)


__all__ = ["act", "view"]
