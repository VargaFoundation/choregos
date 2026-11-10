# SPDX-License-Identifier: Apache-2.0
"""An assistant's data protection (S63-16, ADR 0133 point 6): the reads journal, erasure by user, erasure and
export by subject. The journal and the subject routes are `assistant:audit`'s; the person's own erasure is
open to any member of the organisation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from ..db.models import AssistantInstallation
from ..deps import Db, Me
from ..errors import forbidden
from ..rbac import Permission
from ..schemas.assistant_reads import (
    AssistantReadPage,
    SubjectErasure,
    SubjectErasureRequest,
    UserErasure,
)
from ..schemas.assistants import SLUG
from ..services import assistants
from ..services.agent_governance import is_member
from ..services.assistant_access import holds
from ..services.assistant_erasure import by_subject, erase_user
from ..services.assistant_reads import page_of_reads
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=SLUG)]


async def _audited(session: Db, principal: Me, org: str, slug: str) -> AssistantInstallation:
    organisation = await organisation_by_slug(session, org)
    row = await assistants.installation(session, organisation.id, slug)
    if not holds(principal, Permission.ASSISTANT_AUDIT, org, row):
        raise forbidden(
            f"the data of `{slug}` is audited by its owners, the administrator of {org} and privacy officers"
        )
    return row


@router.get(
    "/orgs/{org}/assistants/{slug}/reads",
    response_model=AssistantReadPage,
    operation_id="listAssistantReads",
)
async def list_reads(  # noqa: PLR0913 - the filters of the journal, one by one
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    subject_type: Annotated[str | None, Query()] = None,
    subject_key: Annotated[str | None, Query()] = None,
    user_id: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AssistantReadPage:
    row = await _audited(session, principal, org, slug)
    return await page_of_reads(
        session,
        row.id,
        subject_type=subject_type,
        subject_key=subject_key,
        user_id=user_id,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/orgs/{org}/assistants/{slug}/erasure",
    response_model=SubjectErasure,
    operation_id="eraseAssistantSubject",
)
async def erase_subject(
    org: str, slug: Slug, body: SubjectErasureRequest, session: Db, principal: Me
) -> SubjectErasure:
    row = await _audited(session, principal, org, slug)
    return await by_subject(session, principal, org, row, body)


@router.delete("/me/assistants/{org}/{slug}", response_model=UserErasure, operation_id="eraseMyAssistantData")
async def erase_my_data(org: str, slug: Slug, session: Db, principal: Me) -> UserErasure:
    if not is_member(principal, org):
        raise forbidden(f"the data of an assistant of {org} is its members'")
    organisation = await organisation_by_slug(session, org)
    row = await assistants.installation(session, organisation.id, slug)
    return await erase_user(session, principal, org, row)
