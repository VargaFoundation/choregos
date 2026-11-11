# SPDX-License-Identifier: Apache-2.0
"""The reads journal of an assistant (S63-16, ADR 0133 point 6): one line per tool call of its conversations.

A line says who the assistant read for, through which need and operation, about which subject and when:
never what it read. The one place conversation tool calls are booked (`_book_the_tool_call`) writes it, in
the same transaction as the call's own booking. `assistant:audit` reads the journal, filtered by subject,
user, need and period and paginated by keyset (the journal can be long: no page is loaded whole).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from choregos_core import utcnow
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionRecord, AssistantRead, AssistantToolBinding, OrgConnector
from ..schemas.assistant_reads import AssistantReadLine, AssistantReadPage
from ..schemas.base import PageMeta
from .assistant_memory import subjects_in

#: What a line says of a subject whose data was erased (`services/assistant_erasure.py`).
ERASED = "[erased]"


async def need_of(session: AsyncSession, installation_id: str, connector_name: str, operation: str) -> str:
    """The need an operation of a connector is bound under for the installation (the connector's name when
    no binding lists it: a tool the model was given another way)."""
    rows = await session.execute(
        select(AssistantToolBinding.need, AssistantToolBinding.operations)
        .join(OrgConnector, OrgConnector.id == AssistantToolBinding.connector_id)
        .where(AssistantToolBinding.installation_id == installation_id, OrgConnector.name == connector_name)
        .order_by(AssistantToolBinding.need)
    )
    for need, operations in rows.all():
        if operation in (operations or []):
            return str(need)
    return connector_name


def subject_of_call(record: AgentSessionRecord, body: Any) -> tuple[str, str] | None:
    """The subject a call is about: the one its result carries, else the one the conversation resolved last
    (the subject in play). `None` when neither exists."""
    found = subjects_in(body.get("structuredContent")) if isinstance(body, dict) else []
    if found:
        return found[0]["type"], found[0]["key"]
    resolved = record.resolved_subjects or []
    if resolved:
        return str(resolved[-1]["type"]), str(resolved[-1]["key"])
    return None


async def journal_read(
    session: AsyncSession, record: AgentSessionRecord, connector_name: str, operation: str, body: Any
) -> None:
    """Writes the line of one tool call of an assistant's conversation; nothing for any other session."""
    if record.kind != "conversation" or not record.installation_id or not record.user_id:
        return
    subject = subject_of_call(record, body)
    session.add(
        AssistantRead(
            org_id=record.org_id,
            installation_id=record.installation_id,
            user_id=record.user_id,
            conversation_id=record.id,
            need=await need_of(session, record.installation_id, connector_name, operation),
            operation=operation,
            subject_type=subject[0] if subject else None,
            subject_key=subject[1] if subject else None,
            at=utcnow(),
        )
    )


def line_of(row: AssistantRead) -> AssistantReadLine:
    return AssistantReadLine(
        id=row.id,
        user_id=row.user_id,
        conversation_id=row.conversation_id,
        work_item_key=row.work_item_key,
        task=row.task,
        need=row.need,
        operation=row.operation,
        subject_type=row.subject_type,
        subject_key=row.subject_key,
        delegation_id=row.delegation_id,
        at=row.at,
    )


async def page_of_reads(  # noqa: PLR0913 - the filters of the journal, one by one
    session: AsyncSession,
    installation_id: str,
    *,
    subject_type: str | None,
    subject_key: str | None,
    user_id: str | None,
    need: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None,
    limit: int,
) -> AssistantReadPage:
    """The installation's lines, most recent first, `cursor` being the id of the last line of the page
    before."""
    query = select(AssistantRead).where(AssistantRead.installation_id == installation_id)
    for column, wanted in (
        (AssistantRead.subject_type, subject_type),
        (AssistantRead.subject_key, subject_key),
        (AssistantRead.user_id, user_id),
    ):
        if wanted is not None:
            query = query.where(column == wanted)
    if need is not None:
        query = query.where(AssistantRead.need == need)
    if since is not None:
        query = query.where(AssistantRead.at >= since)
    if until is not None:
        query = query.where(AssistantRead.at < until)
    after: Any = None
    if cursor:
        after = await session.get(AssistantRead, cursor)
    if after is not None:
        query = query.where(
            or_(
                AssistantRead.at < after.at,
                and_(AssistantRead.at == after.at, AssistantRead.id < after.id),
            )
        )
    rows = list(
        (
            await session.execute(
                query.order_by(AssistantRead.at.desc(), AssistantRead.id.desc()).limit(limit + 1)
            )
        ).scalars()
    )
    window = rows[:limit]
    has_more = len(rows) > limit
    return AssistantReadPage(
        items=[line_of(r) for r in window],
        meta=PageMeta(next_cursor=window[-1].id if has_more and window else None, has_more=has_more),
    )
