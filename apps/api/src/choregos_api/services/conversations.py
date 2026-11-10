# SPDX-License-Identifier: Apache-2.0
"""Durable conversations (S63-04, ADR 0134 points 2-5): a session that outlives its key.

A conversation is an `agent_sessions` row of kind `conversation`, in its opener's personal space. It holds
no gateway key: each turn reserves from what is left of the conversation's cap (and from the caps a run
meets, `reserve_budget`), mints a key `turn:<conversation>:<n>`, runs the prompt loop, and at its end reads
the key's spend, books it in the ledger with `session_id`, revokes the key. A turn the cap refuses is refused
before the model is called.

A conversation is `idle` once `idle_close_minutes` (240 by default) passed since its last activity. That
state is derived from the clock, never swept: the next message reopens it with its history, and says so
in the journal (`conversation.resumed`). It keeps the agent revision it started with.

Not done here, stated in the ADR: the answer is computed whole and sent as one delta (the prompt loop does
not stream yet), the history is not summarised past a token threshold, `retention_until` is not set (no
installation policy exists yet), and the title is a deterministic cut of the first message, not a model's.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from choregos_core import utcnow
from choregos_core.domain import aware
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    AgentSessionEvent,
    AgentSessionRecord,
    CostLedger,
    GatewayKeyRow,
    Organization,
    Project,
    User,
)
from ..errors import ApiError, conflict, not_found
from ..logging import get_logger
from ..schemas.agent_sessions import (
    AgentDelta,
    AgentResponse,
    Conversation,
    ConversationMessage,
    ConversationSummary,
)
from .agent_sessions import (
    DEFAULT_SESSION_USD,
    ConversationTerms,
    journal,
    mint_key,
    open_session,
    reserve_budget,
    respond,
    session_gateway,
)
from .personal_spaces import personal_space

if TYPE_CHECKING:
    from ..rbac import Principal

log = get_logger(__name__)

#: Minutes of inactivity after which a conversation is idle, when its opener names none.
DEFAULT_IDLE_CLOSE_MINUTES = 240
#: What a conversation may spend in all when its opener names no cap (USD).
DEFAULT_CONVERSATION_USD = 5.0
#: A turn's key lives this long at most: the turn's own length, not the conversation's.
TURN_KEY_TTL_S = 1800
TITLE_CHARS = 60


async def open_conversation(
    session: AsyncSession,
    principal: Principal,
    organisation: Organization,
    agent_slug: str,
    *,
    max_usd: float | None = None,
    idle_close_minutes: int | None = None,
    context: dict[str, Any] | None = None,
    installation_id: str | None = None,
) -> AgentSessionRecord:
    """A conversation with the agent in the caller's personal space, created on first use (S63-03).

    Nothing is reserved and no key is minted: the first turn does. Who may talk to which agent (the
    installation's audience) is the caller's to check before; this opens what it is asked to."""
    user = await session.get(User, principal.user_id)
    if user is None:
        raise not_found("User", principal.user_id)
    space = await personal_space(session, organisation, user)
    return await open_session(
        session,
        principal,
        organisation,
        agent_slug,
        space,
        max_usd=None,
        conversation=ConversationTerms(
            max_usd=max_usd if max_usd is not None else DEFAULT_CONVERSATION_USD,
            idle_close_minutes=idle_close_minutes or DEFAULT_IDLE_CLOSE_MINUTES,
            context=context,
            installation_id=installation_id,
        ),
    )


def last_activity(record: AgentSessionRecord) -> datetime:
    return aware(record.last_activity_at or record.created_at) or utcnow()


def is_idle(record: AgentSessionRecord, now: datetime | None = None) -> bool:
    window = timedelta(minutes=record.idle_close_minutes or DEFAULT_IDLE_CLOSE_MINUTES)
    return record.status == "open" and (now or utcnow()) - last_activity(record) >= window


def effective_status(record: AgentSessionRecord) -> str:
    return "idle" if is_idle(record) else record.status


def derive_title(text: str) -> str:
    """A title cut from the first message: its first line, cut at a word. Deterministic, costs nothing
    (ADR 0134 asks for the model's own title within the turn's key; that is not done yet)."""
    line = " ".join(text.strip().splitlines()[0].split()) if text.strip() else ""
    if len(line) <= TITLE_CHARS:
        return line or "Untitled conversation"
    cut = line[:TITLE_CHARS].rsplit(" ", 1)[0] or line[:TITLE_CHARS]
    return cut.rstrip(" ,;:.") + "..."


async def converse(session: AsyncSession, record: AgentSessionRecord, text: str) -> AgentResponse:
    """One turn: reopen an idle conversation, reserve, mint the turn's key, run the loop, settle the key."""
    from .agents import resolve_agent
    from .assistant_conversations import assistant_caps
    from .budgets import DEFAULT_FLOOR_USD

    if record.kind != "conversation" or record.status not in ("open", "idle"):
        raise conflict(f"conversation {record.id} is {record.status} ({record.reason}): open a new one")
    project = await session.get(Project, record.project_id)
    if project is None:
        raise not_found("Project", record.project_id)
    now = utcnow()
    if is_idle(record, now) or record.status == "idle":
        idle_for = int((now - last_activity(record)).total_seconds())
        record.status = "open"
        await journal(session, record, "conversation.resumed", {"idle_seconds": idle_for})
    # The turn is activity: the deadline `respond` reads moves before it, and again when the turn ends.
    record.expires_at = now + timedelta(minutes=record.idle_close_minutes or DEFAULT_IDLE_CLOSE_MINUTES)
    cap = record.max_usd if record.max_usd is not None else DEFAULT_SESSION_USD
    left = round(cap - record.cost_usd, 6)
    if left < DEFAULT_FLOOR_USD:
        raise ApiError(
            409,
            "Budget exhausted",
            f"the conversation's cap of ${cap:.2f} is spent (${record.cost_usd:.4f}): no turn is taken",
        )
    # The revision the conversation started with, not whatever the project serves today.
    resolved = await resolve_agent(
        session, record.org_id, project.id, f"{record.agent_slug}@{record.agent_version}"
    )
    reservation = await reserve_budget(
        session,
        record.org_id,
        project,
        record.agent_slug,
        resolved.spec,
        left,
        extra_caps=await assistant_caps(session, record),
    )
    if reservation.below(DEFAULT_FLOOR_USD):
        raise ApiError(409, "Budget exhausted", f"the turn does not start: {reservation.describe()}")
    record.turns += 1
    number = record.turns
    gateway = await session_gateway(session, record.org_id)
    key = await mint_key(
        session,
        gateway,
        record,
        project,
        budget_usd=reservation.usd,
        ttl_s=TURN_KEY_TTL_S,
        run_id=f"turn:{record.id}:{number}",
    )
    await journal(
        session,
        record,
        "turn.started",
        {"turn": number, "reserved_usd": reservation.usd, "bound_by": reservation.bound_by},
    )
    first = not record.messages or all(m.get("role") == "system" for m in record.messages)
    try:
        answer = await respond(session, record, project, [text], turn_key=str(key.key))
    finally:
        await _settle_turn(session, record, gateway, key.key_id, number)
    if first and answer.status == "completed" and record.title is None:
        record.title = derive_title(text)
    stamped = utcnow()
    record.last_activity_at = stamped
    record.expires_at = stamped + timedelta(minutes=record.idle_close_minutes or DEFAULT_IDLE_CLOSE_MINUTES)
    return answer


async def _settle_turn(
    session: AsyncSession, record: AgentSessionRecord, gateway: Any, key_id: str, number: int
) -> None:
    """The turn's key ends with the turn: its spend booked with `session_id`, the key revoked. A failure
    here is logged, not raised: the key still dies at its TTL, and the turn's answer is not lost."""
    from ..config import get_settings
    from .costs import record_cost
    from .model_catalogue import priced_spend

    try:
        spend = await gateway.spend(key_id)
        spend, _priced = await priced_spend(
            session,
            record.org_id,
            [record.model, *spend.models_used],
            spend,
            measured="spend_readback" in getattr(gateway, "capabilities", {"spend_readback"}),
        )
        entry = await record_cost(
            session,
            project_id=record.project_id,
            work_item_id=None,
            run_id=None,
            provider=spend.models_used[0].split("/")[0] if spend.models_used else "",
            model=record.model,
            backend="prompt",
            stage_role=None,
            size=None,
            tokens_in=spend.tokens_in,
            tokens_out=spend.tokens_out,
            tokens_cached=spend.tokens_cached,
            cost_usd=spend.cost_usd,
            fx_rate=get_settings().fx_usd_eur,
        )
        entry.session_id = record.id
        entry.installation_id, entry.user_id = record.installation_id, record.user_id
        entry.agent_slug, entry.agent_version = record.agent_slug, record.agent_version
        record.cost_usd = round(record.cost_usd + spend.cost_usd, 6)
        revoked = True
        try:
            await gateway.revoke(key_id)
        except Exception as error:  # a cleanup: the key still dies at its TTL
            log.warning("conversation.key_not_revoked", session_id=record.id, error=str(error)[:300])
            revoked = False
        row = (
            await session.execute(select(GatewayKeyRow).where(GatewayKeyRow.key_id == key_id))
        ).scalar_one_or_none()
        if row is not None:
            row.revoked, row.spend_usd = revoked, spend.cost_usd
        await journal(
            session, record, "turn.ended", {"turn": number, "cost_usd": spend.cost_usd, "revoked": revoked}
        )
    except Exception as failure:
        log.warning(
            "conversation.turn_not_settled", session_id=record.id, turn=number, error=type(failure).__name__
        )


async def delete_conversation(session: AsyncSession, record: AgentSessionRecord) -> None:
    """Erases the conversation and its transcript. Its ledger lines stay (the money was spent) and lose
    their `session_id`, as the foreign key's `SET NULL` says; done here too for a database without it."""
    await session.execute(
        update(CostLedger).where(CostLedger.session_id == record.id).values(session_id=None)
    )
    await session.execute(delete(AgentSessionEvent).where(AgentSessionEvent.session_id == record.id))
    await session.delete(record)
    await session.flush()


def summary_of(record: AgentSessionRecord) -> ConversationSummary:
    return ConversationSummary(
        id=record.id,
        title=record.title,
        agent=record.agent_slug,
        agent_version=record.agent_version,
        installation_id=record.installation_id,
        status=effective_status(record),
        turns=record.turns,
        cost_usd=record.cost_usd,
        max_usd=record.max_usd,
        created_at=record.created_at,
        last_activity_at=last_activity(record),
    )


def conversation_of(record: AgentSessionRecord) -> Conversation:
    """The summary and what was said: the system message and the tool traffic stay out."""
    said = [
        ConversationMessage(role=m["role"], content=str(m["content"]))
        for m in record.messages or []
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    return Conversation(**summary_of(record).model_dump(), context=record.context, messages=said)


async def response_events(answer: AgentResponse) -> AsyncIterator[dict[str, str]]:
    """The server-sent events of a computed answer: created, one delta with the whole text, then its end."""
    created = answer.model_copy(update={"output": [], "output_text": ""})
    yield {"event": "response.created", "data": created.model_dump_json()}
    if answer.output_text:
        yield {
            "event": "response.output_text.delta",
            "data": AgentDelta(response_id=answer.id, delta=answer.output_text).model_dump_json(),
        }
    yield {"event": f"response.{answer.status}", "data": answer.model_dump_json()}
