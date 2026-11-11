# SPDX-License-Identifier: Apache-2.0
"""The assistant a conversation belongs to, as the console needs to name it (S63-18).

A conversation carries `installation_id`; a screen needs the organisation's slug (feedback, memory proposals
and the connect links are routes of an organisation) and the installation's slug. They are read here, in
one query for a page of conversations, and written onto the summaries: a conversation opened from anywhere
(the list, a link, the embedded component) shows the same assistant's memory proposals.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionRecord, AssistantInstallation, Organization
from ..schemas.agent_sessions import ConversationSummary


async def with_assistant[T: ConversationSummary](
    session: AsyncSession, records: list[AgentSessionRecord], items: list[T]
) -> list[T]:
    """`items` (the summaries of `records`, same order) with `org` always set and `assistant` set when the
    conversation is an assistant's."""
    org_ids = {r.org_id for r in records}
    installation_ids = {r.installation_id for r in records if r.installation_id}
    orgs: dict[str, str] = {}
    if org_ids:
        found = await session.execute(
            select(Organization.id, Organization.slug).where(Organization.id.in_(org_ids))
        )
        orgs = {row[0]: row[1] for row in found.all()}
    slugs: dict[str, str] = {}
    if installation_ids:
        found = await session.execute(
            select(AssistantInstallation.id, AssistantInstallation.slug).where(
                AssistantInstallation.id.in_(installation_ids)
            )
        )
        slugs = {row[0]: row[1] for row in found.all()}
    return [
        item.model_copy(
            update={
                "org": orgs.get(record.org_id),
                "assistant": slugs.get(record.installation_id) if record.installation_id else None,
            }
        )
        for record, item in zip(records, items, strict=True)
    ]
