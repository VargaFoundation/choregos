# SPDX-License-Identifier: Apache-2.0
"""What a turn leaves for the MCP door's answer (ADR 0139 point 3): `citations` and `missing_accounts`.

Both come from facts of the turn, written to the conversation's journal as they happen, and read back
from the journal after the turn's last `turn.started`: so any process answers `get_conversation` the
same, and nothing is kept in memory.

- `knowledge.cited`: a knowledge tool returned a passage or a document (id, title, version, base). It is
  what the assistant was GIVEN to cite; the citation rule asks it to name what it used.
- `memory.written`: the turn wrote or proposed in a memory space (its name): the answer's
  `memory_updated`, which the embedded component turns into `memory.updated`.
- `account.missing`: a tool of a `user_consent` need answered `account_not_connected`: the need and the
  console's connect URL.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionEvent, AgentSessionRecord
from ..schemas.agent_sessions import AgentResponse

CITED = "knowledge.cited"
ACCOUNT_MISSING = "account.missing"
MEMORY_WRITTEN = "memory.written"


async def note_citations(
    session: AsyncSession, record: AgentSessionRecord, cited: list[dict[str, str]]
) -> None:
    from .agent_sessions import journal

    for item in cited:
        await journal(session, record, CITED, item)


async def note_missing_account(
    session: AsyncSession, record: AgentSessionRecord, need: str, connect_url: str
) -> None:
    from .agent_sessions import journal

    await journal(session, record, ACCOUNT_MISSING, {"need": need, "connect_url": connect_url})


async def signals_of(
    session: AsyncSession, conversation_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`(citations, missing_accounts)` of the conversation's latest turn, without repeats, in order."""
    started = (
        await session.execute(
            select(func.coalesce(func.max(AgentSessionEvent.seq), 0)).where(
                AgentSessionEvent.session_id == conversation_id, AgentSessionEvent.type == "turn.started"
            )
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(AgentSessionEvent.type, AgentSessionEvent.payload)
            .where(
                AgentSessionEvent.session_id == conversation_id,
                AgentSessionEvent.seq > started,
                AgentSessionEvent.type.in_([CITED, ACCOUNT_MISSING]),
            )
            .order_by(AgentSessionEvent.seq)
        )
    ).all()
    citations: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []
    for type_, payload in rows:
        item = dict(payload or {})
        target = citations if type_ == CITED else accounts
        if item not in target:
            target.append(item)
    return citations, accounts


async def note_memory_written(session: AsyncSession, record: AgentSessionRecord, space: str) -> None:
    from .agent_sessions import journal

    await journal(session, record, MEMORY_WRITTEN, {"space": space})


async def memory_written_of(session: AsyncSession, conversation_id: str) -> list[str]:
    """The memory spaces the conversation's latest turn wrote or proposed in, without repeats, in order."""
    started = (
        await session.execute(
            select(func.coalesce(func.max(AgentSessionEvent.seq), 0)).where(
                AgentSessionEvent.session_id == conversation_id, AgentSessionEvent.type == "turn.started"
            )
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(AgentSessionEvent.payload)
            .where(
                AgentSessionEvent.session_id == conversation_id,
                AgentSessionEvent.seq > started,
                AgentSessionEvent.type == MEMORY_WRITTEN,
            )
            .order_by(AgentSessionEvent.seq)
        )
    ).scalars()
    spaces: list[str] = []
    for payload in rows:
        name = str((payload or {}).get("space", ""))
        if name and name not in spaces:
            spaces.append(name)
    return spaces


async def pending_actions_of(session: AsyncSession, conversation_id: str) -> list[dict[str, Any]]:
    """The writes the assistant proposed and the person has not decided, each with the console link where
    they decide. The MCP door and the REST turn both answer with this list (ADR 0154 §4, G1): they
    list them, they never execute one (ADR 0030)."""
    from ..tenant_host import org_base_url_of_id
    from .conversation_actions import actions_of, confirm_url

    record = await session.get(AgentSessionRecord, conversation_id)
    base = await org_base_url_of_id(session, record.org_id if record is not None else None)
    return [
        {"id": a.id, "summary": a.summary, "confirm_url": confirm_url(conversation_id, a.id, base)}
        for a in await actions_of(session, conversation_id, status="proposed")
    ]


async def with_signals(session: AsyncSession, response: AgentResponse) -> AgentResponse:
    """The REST turn's answer says what the door's says for the same turn: the same three lists, from the
    same reads (`signals_of`, `pending_actions_of`)."""
    from ..schemas.agent_sessions import AgentCitation, AgentMissingAccount, AgentPendingAction

    citations, accounts = await signals_of(session, response.session_id)
    response.citations = [AgentCitation.model_validate(c) for c in citations]
    response.missing_accounts = [AgentMissingAccount.model_validate(a) for a in accounts]
    response.pending_actions = [
        AgentPendingAction.model_validate(p) for p in await pending_actions_of(session, response.session_id)
    ]
    return response
