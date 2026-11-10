# SPDX-License-Identifier: Apache-2.0
"""A person's memory page of an assistant (ADR 0136 point 6, S63-09): see, confirm or reject, correct, pin,
delete. Owner only: a space is its person's, and only the member of the organisation reaches it."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from ..deps import Db, Me
from ..errors import forbidden, not_found
from ..schemas.assistant_memory import MemoryAction, MemorySpace
from ..schemas.assistants import SLUG
from ..services import assistant_memory as memory
from ..services import assistant_memory_page as page
from ..services import assistants
from ..services.agent_governance import is_member
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=SLUG)]
Space = Annotated[str, Path(pattern=r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")]


async def _spaces(session: Db, principal: Me, org: str, slug: str) -> memory.Spaces:
    if not is_member(principal, org):
        raise forbidden(f"the memory of an assistant of {org} is its members'")
    organisation = await organisation_by_slug(session, org)
    row = await assistants.installation(session, organisation.id, slug)
    found = await memory.spaces_of(session, row, principal.user_id)
    if found is None:
        raise not_found("Assistant memory", slug)
    return found


@router.get(
    "/me/assistants/{org}/{slug}/memory/{space}",
    response_model=MemorySpace,
    operation_id="getMyAssistantMemory",
)
async def get_memory(org: str, slug: Slug, space: Space, session: Db, principal: Me) -> MemorySpace:
    return await page.view(session, await _spaces(session, principal, org, slug), space)


@router.get(
    "/me/assistants/{org}/{slug}/memory/{space}/{subject}",
    response_model=MemorySpace,
    operation_id="getMyAssistantSubjectMemory",
)
async def get_subject_memory(
    org: str, slug: Slug, space: Space, subject: str, session: Db, principal: Me
) -> MemorySpace:
    return await page.view(session, await _spaces(session, principal, org, slug), space, subject)


@router.patch(
    "/me/assistants/{org}/{slug}/memory/{space}",
    response_model=MemorySpace,
    operation_id="updateMyAssistantMemory",
)
async def update_memory(
    org: str, slug: Slug, space: Space, body: MemoryAction, session: Db, principal: Me
) -> MemorySpace:
    return await page.act(session, await _spaces(session, principal, org, slug), space, None, body)


@router.patch(
    "/me/assistants/{org}/{slug}/memory/{space}/{subject}",
    response_model=MemorySpace,
    operation_id="updateMyAssistantSubjectMemory",
)
async def update_subject_memory(
    org: str, slug: Slug, space: Space, subject: str, body: MemoryAction, session: Db, principal: Me
) -> MemorySpace:
    return await page.act(session, await _spaces(session, principal, org, slug), space, subject, body)


@router.delete(
    "/me/assistants/{org}/{slug}/memory/{space}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteMyAssistantMemory",
)
async def delete_memory(
    org: str,
    slug: Slug,
    space: Space,
    session: Db,
    principal: Me,
    fact_id: Annotated[str | None, Query()] = None,
) -> Response:
    await page.forget(session, await _spaces(session, principal, org, slug), space, None, fact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/me/assistants/{org}/{slug}/memory/{space}/{subject}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteMyAssistantSubjectMemory",
)
async def delete_subject_memory(
    org: str,
    slug: Slug,
    space: Space,
    subject: str,
    session: Db,
    principal: Me,
    fact_id: Annotated[str | None, Query()] = None,
) -> Response:
    await page.forget(session, await _spaces(session, principal, org, slug), space, subject, fact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
