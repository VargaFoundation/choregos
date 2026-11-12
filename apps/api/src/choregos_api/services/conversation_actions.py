# SPDX-License-Identifier: Apache-2.0
"""Writes proposed in a conversation, decided by the person (S63-10, ADR 0139 point 5, ADR 0030).

An assistant conversation is offered the `inline` write needs of its installation, as `<need>__<operation>`
(the need-to-tool mapping is `conversation_needs`, shared with the read tools).
Calling one executes NOTHING: it stores a `ConversationAction` (`proposed`, the arguments as the agent gave
them, a readable summary) and tells the agent the action awaits the person's confirmation. The conversation's
owner confirms it (`confirm`: the broker calls the source with THE PERSON'S identity, the source staying the
judge of the right to write) or refuses it (`refuse`). Neither the agent nor the MCP door ever executes.

What is offered, and why not more:
- a binding whose approval is `inline` and whose identity is `user_exchange` (S63-07) or `user_consent`
  (S63-08), `conversation_needs.EXECUTABLE_IDENTITIES`: the person confirms in the console and the broker
  executes it as them (an exchanged token, or the person's connected account; without an account the call is
  refused `account_not_connected` and the action stays confirmed-but-failed like any refused call).
- a binding whose approval is `console` and whose identity is `service`: the call proposes a GOVERNED action
  (ADR 0035, `broker.tool_action`) in the person's personal space, decided at `/p/<space>/actions/<id>` by
  whoever
  the project's rules name, with a fresh sign-in, played by the `ActionWorkflow` under the connector's own
  identity. The conversation's row is `governed`, its `result` names the action, and its status follows the
  action's. A `console` need with the identity `user_exchange` is NOT offered: the governed action's effect
  has no
  per-person identity, so it would write as the service where the binding says the source judges the person.
  An inline action proposed before the owners raised the approval is refused at confirmation
  (`approval_console`).
- an operation whose organisation policy is `approval` (a governed action already) is not offered either.
- the diff: when the need also serves a read operation `<operation>_preview` (`conversation_needs.previews`),
  the proposal calls it with the same arguments, as the person, and keeps what it returned in `diff`.
- a confirmed call is booked like any tool call of a conversation (`_book`): a ledger line (installation and
  person named), the conversation's tool-call count, and a `tool.called` event.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from choregos_contracts import ActionStatus, EventType
from choregos_core import utcnow
from choregos_core.domain import aware
from choregos_core.prompt_loop import LoopTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record as audit
from ..config import get_settings
from ..db.models import Action, AgentSessionRecord, ConversationAction, CostLedger, Project, User
from ..errors import ApiError, conflict, not_found
from ..rbac import Principal
from ..schemas import AgentSpec
from ..schemas.conversation_actions import ConversationAction as ConversationActionDto
from .agent_sessions import journal, tool_parameters
from .broker import (
    ArgumentsOutsideGrant,
    ArgumentsRefused,
    BrokerTool,
    call,
    call_id,
    check_arguments,
    tool_action,
)
from .conversation_needs import need_identities, previews, write_tools
from .events import persist_event
from .identity_exchange import SERVICE, USER_CONSENT, USER_EXCHANGE, Acting
from .mcp_gateway import RouteRefusal, route_of

#: A proposal nobody decided in this time is `expired`.
EXPIRES_AFTER = timedelta(hours=24)
SUMMARY_CHARS = 300


@dataclass(frozen=True, slots=True)
class Proposable:
    """An inline write need's operation the conversation may propose."""

    name: str
    need: str
    tool: BrokerTool
    #: A `console` approval: the call proposes a governed action instead of a confirmation of the person's.
    governed: bool = False
    #: The read operation that previews this write at the source, when it serves one.
    preview: BrokerTool | None = None

    def loop_tool(self) -> LoopTool:
        how = (
            "a person with the right approves it in the console"
            if self.governed
            else "the person confirms or refuses it in the console"
        )
        description = (
            f"{self.tool.operation.description or self.tool.operation.name} - a WRITE: calling it does not "
            f"run it, it proposes it: {how}"
        )
        return LoopTool(self.name, description, tool_parameters(self.tool))


def summary_of(need: str, operation: str, arguments: dict[str, Any]) -> str:
    shown = ", ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in arguments.items())
    text = f"{need}: {operation}({shown})"
    return text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 1] + "..."


async def proposable(
    session: AsyncSession,
    record: AgentSessionRecord,
    spec: AgentSpec,
    project: Project,
    *,
    approval: str = "inline",
) -> dict[str, Proposable]:
    """What a turn may propose with a person's confirmation: the writes of the needs `conversation_needs`
    maps, with this approval."""
    found = await write_tools(session, record, spec, project, approval=approval)
    shown = await previews(session, record, spec, project)
    return {
        name: Proposable(name, need, tool, preview=shown.get(name)) for name, (need, tool) in found.items()
    }


async def governed_proposable(
    session: AsyncSession, record: AgentSessionRecord, spec: AgentSpec, project: Project
) -> dict[str, Proposable]:
    """The writes of `console` needs bound with the connector's own identity: proposed as governed actions."""
    found = await write_tools(session, record, spec, project, approval="console", identities=(SERVICE,))
    shown = await previews(session, record, spec, project)
    return {
        name: Proposable(name, need, tool, governed=True, preview=shown.get(name))
        for name, (need, tool) in found.items()
    }


async def offered_writes(
    session: AsyncSession, record: AgentSessionRecord, spec: AgentSpec, project: Project
) -> dict[str, Proposable]:
    """Every write a turn may propose: the person's confirmations and the governed actions."""
    return {
        **await proposable(session, record, spec, project),
        **await governed_proposable(session, record, spec, project),
    }


async def _acting(session: AsyncSession, record: AgentSessionRecord, connector: str) -> Acting:
    """The person of the conversation: through the identity exchange, or under their connected account when
    the need's binding says `user_consent` (the broker serves both, `acting=`)."""
    mode = (await need_identities(session, record)).get(connector, USER_EXCHANGE)
    user = await session.get(User, record.user_id) if record.user_id else None
    return Acting(
        USER_CONSENT if mode == USER_CONSENT else USER_EXCHANGE,
        user_id=record.user_id or "",
        subject=(user.oidc_sub if user is not None else None) or record.user_id or "",
        org_id=record.org_id,
        actor=f"assistant:{record.agent_slug}",
    )


async def _book(
    session: AsyncSession,
    record: AgentSessionRecord,
    project: Project,
    tool: BrokerTool,
    code: int,
    body: Any,
    acting: Acting,
) -> None:
    """A call of the broker made for the conversation, booked like a tool call of a turn: the ledger line
    (the installation and the person named, so the month caps read it), the count and the `tool.called`
    event. A call the network policy stopped never left: it is not booked."""
    if isinstance(body, dict) and body.get("type") in {"network_policy", "rate_limited"}:
        return
    provider = f"mcp:{tool.connector.name}"
    record.tool_calls += 1
    session.add(
        CostLedger(
            project_id=project.id,
            session_id=record.id,
            installation_id=record.installation_id,
            user_id=record.user_id,
            ts=utcnow(),
            kind="tool",
            provider=provider,
            model=tool.name,
            cost_usd=tool.operation.price_usd or 0.0,
            agent_slug=record.agent_slug,
            agent_version=record.agent_version,
        )
    )
    await persist_event(
        session,
        EventType.TOOL_CALLED,
        project_id=project.id,
        project_slug=project.slug,
        subject=record.id,
        session_id=record.id,
        tool=tool.name,
        provider=provider,
        status_code=code,
        **acting.audit(tool.connector.name),
    )


async def _diff(
    session: AsyncSession, record: AgentSessionRecord, entry: Proposable, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """What the source's preview of the write says, as the person; `None` when it serves none or fails (the
    proposal is made all the same: a preview is an aid, not a condition)."""
    if entry.preview is None:
        return None
    project = await session.get(Project, record.project_id) if record.project_id else None
    if project is None:
        return None
    acting = await _acting(session, record, entry.preview.connector.name)
    code, body = await call(session, entry.preview, arguments, project=project, acting=acting)
    await _book(session, record, project, entry.preview, code, body, acting)
    if code != 200 or not isinstance(body, dict):
        return None
    structured = body.get("structuredContent")
    if isinstance(structured, dict):
        return dict(structured)
    texts = [p.get("text") for p in body.get("content") or [] if isinstance(p, dict) and p.get("text")]
    return {"text": "\n".join(str(t) for t in texts)} if texts else None


async def _propose_governed(
    session: AsyncSession, record: AgentSessionRecord, entry: Proposable, arguments: dict[str, Any]
) -> ConversationAction | str:
    """A `console` need's write, as a governed action in the person's personal space (ADR 0035)."""
    project = await session.get(Project, record.project_id) if record.project_id else None
    if project is None:
        return "action not proposed: the conversation has no space to act in"
    try:
        await route_of(session, entry.tool.connector)
    except RouteRefusal as refused:
        return f"action not proposed: {refused}"
    governed = await tool_action(
        session,
        project,
        entry.tool,
        arguments,
        action_id=call_id(record.id, entry.tool.name, arguments),
        proposed_by={"kind": "agent", "id": f"agent:{record.agent_slug}", "conversation_id": record.id},
        justification=f"proposed by the assistant {record.agent_slug} in conversation {record.id}",
    )
    row = ConversationAction(
        org_id=record.org_id,
        conversation_id=record.id,
        user_id=record.user_id or "",
        need=entry.need,
        operation=entry.tool.operation.name,
        arguments=arguments,
        summary=summary_of(entry.need, entry.tool.operation.name, arguments),
        status="governed",
        result={"action": governed.id, "decision_path": f"/p/{project.slug}/actions/{governed.id}"},
    )
    session.add(row)
    return row


async def propose(
    session: AsyncSession,
    record: AgentSessionRecord,
    offered: dict[str, Proposable],
    name: str,
    arguments: dict[str, Any],
) -> str:
    """The agent called a write: nothing is executed, an action is stored and the agent is told so."""
    entry = offered[name]
    try:
        check_arguments(entry.tool, arguments)
    except (ArgumentsRefused, ArgumentsOutsideGrant) as refusal:
        return f"action not proposed: {refusal}"
    operation = entry.tool.operation.name
    diff = await _diff(session, record, entry, arguments)
    if entry.governed:
        proposed = await _propose_governed(session, record, entry, arguments)
        if isinstance(proposed, str):
            return proposed
        action = proposed
        action.diff = diff
        await session.flush()
        await journal(
            session,
            record,
            "action.proposed",
            {"action_id": action.id, "need": entry.need, "operation": operation, "summary": action.summary},
        )
        return (
            f"Action `{action.id}` is proposed as a governed action and awaits an approval in the console: "
            f"{action.summary}. NOTHING was executed. Tell the person what you propose and that it must be "
            "approved there; do not call the tool again for the same action."
        )
    action = ConversationAction(
        org_id=record.org_id,
        conversation_id=record.id,
        user_id=record.user_id or "",
        need=entry.need,
        operation=operation,
        arguments=arguments,
        summary=summary_of(entry.need, operation, arguments),
        diff=diff,
    )
    session.add(action)
    await session.flush()
    await journal(
        session,
        record,
        "action.proposed",
        {"action_id": action.id, "need": entry.need, "operation": operation, "summary": action.summary},
    )
    return (
        f"Action `{action.id}` is proposed and awaits the person's confirmation: {action.summary}. "
        "NOTHING was executed. Tell the person what you propose and that they confirm or refuse it "
        "in the console; do not call the tool again for the same action."
    )


def to_dto(action: ConversationAction) -> ConversationActionDto:
    return ConversationActionDto(
        id=action.id,
        conversation_id=action.conversation_id,
        need=action.need,
        operation=action.operation,
        arguments=dict(action.arguments),
        summary=action.summary,
        status=action.status,
        result=dict(action.result) if action.result is not None else None,
        diff=dict(action.diff) if action.diff is not None else None,
        confirmed_by=action.confirmed_by,
        confirmed_at=action.confirmed_at,
        executed_at=action.executed_at,
        created_at=action.created_at,
    )


async def actions_of(
    session: AsyncSession, conversation_id: str, *, status: str | None = None
) -> list[ConversationAction]:
    query = select(ConversationAction).where(ConversationAction.conversation_id == conversation_id)
    if status is not None:
        query = query.where(ConversationAction.status == status)
    ordered = query.order_by(ConversationAction.created_at, ConversationAction.id)
    rows = list((await session.execute(ordered)).scalars())
    for row in rows:
        if row.status == "governed":
            await _follow_governed(session, row)
    return rows


#: What a governed action's status says of the conversation's row.
_FOLLOWS = {
    ActionStatus.SUCCEEDED.value: "executed",
    ActionStatus.AWAITING_EVIDENCE.value: "executed",
    ActionStatus.REJECTED.value: "refused",
    ActionStatus.FAILED.value: "confirmed",
}


async def _follow_governed(session: AsyncSession, row: ConversationAction) -> None:
    """The row of a governed action takes the action's outcome (`confirmed` without `executed_at`: it
    failed)."""
    governed = await session.get(Action, str((row.result or {}).get("action") or ""))
    if governed is None:
        return
    row.result = {**(row.result or {}), "action_status": governed.status}
    follows = _FOLLOWS.get(governed.status)
    if follows is None:
        return
    row.status = follows
    row.confirmed_at = row.confirmed_at or governed.finished_at
    if follows == "executed":
        row.executed_at = governed.finished_at


def confirm_url(conversation_id: str, action_id: str) -> str:
    """Where the person decides: the console, never the door."""
    base = get_settings().public_url.rstrip("/")
    return f"{base}/conversations/{conversation_id}?action={action_id}"


async def _action(session: AsyncSession, record: AgentSessionRecord, action_id: str) -> ConversationAction:
    action = (
        await session.execute(
            select(ConversationAction)
            .where(ConversationAction.id == action_id, ConversationAction.conversation_id == record.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if action is None:
        raise not_found("Action", action_id)
    return action


def _typed(detail: str, code: str) -> ApiError:
    return conflict(detail, extra={"code": code})


def _late(action: ConversationAction) -> bool:
    return action.status == "proposed" and utcnow() - aware(action.created_at) > EXPIRES_AFTER  # type: ignore[operator]


def _tell_the_conversation(record: AgentSessionRecord, text: str) -> None:
    """The next turn knows what became of the proposal."""
    record.messages = [*(record.messages or []), {"role": "system", "content": text}]


async def confirm(
    session: AsyncSession, record: AgentSessionRecord, action_id: str, principal: Principal
) -> ConversationAction:
    """Execute the action through the broker with the person's identity. The caller checked the owner."""
    action = await _action(session, record, action_id)
    if action.status == "governed":
        raise _typed(
            "this action needs a console approval: it is decided as a governed action, not confirmed here",
            "governed",
        )
    if action.status in ("executed", "refused", "expired"):
        raise _typed(f"action {action.id} is {action.status}", f"action_{action.status}")
    if _late(action):
        action.status = "expired"
        await session.commit()
        raise _typed(f"action {action.id} expired: the assistant must propose it again", "action_expired")
    project = await session.get(Project, record.project_id) if record.project_id else None
    if project is None:
        raise _typed("the conversation has no space to act in", "no_project")
    from .agents import resolve_agent

    resolved = await resolve_agent(
        session, record.org_id, project.id, f"{record.agent_slug}@{record.agent_version}"
    )
    spec: AgentSpec = resolved.spec
    # Read again at confirmation: the owners may have raised the approval or switched the need off since.
    inline = await proposable(session, record, spec, project)
    entry = next(
        (e for e in inline.values() if e.need == action.need and e.tool.operation.name == action.operation),
        None,
    )
    if entry is None:
        console = await proposable(session, record, spec, project, approval="console")
        if any(e.need == action.need for e in console.values()):
            raise _typed(
                "this need now requires a console approval (a governed action), which a conversation "
                "does not run: the action is not executed",
                "approval_console",
            )
        raise _typed(
            f"`{action.need}` is no longer bound for an inline write: the action is not executed",
            "not_available",
        )
    acting = await _acting(session, record, entry.tool.connector.name)
    action.confirmed_by, action.confirmed_at, action.status = principal.user_id, utcnow(), "confirmed"
    code, body = await call(session, entry.tool, dict(action.arguments), project=project, acting=acting)
    action.result = {"status_code": code, "body": body}
    await _book(session, record, project, entry.tool, code, body, acting)
    if code == 200:
        action.status, action.executed_at = "executed", utcnow()
    await journal(
        session,
        record,
        "action.confirmed",
        {
            "action_id": action.id,
            "status": action.status,
            "status_code": code,
            **acting.audit(entry.tool.connector.name),
        },
    )
    await audit(
        session,
        principal,
        "assistant.action.confirm",
        org_id=record.org_id,
        target_type="conversation_action",
        target_id=action.id,
        status=action.status,
    )
    outcome = (
        "it was executed." if action.status == "executed" else f"the source answered {code}: not executed."
    )
    _tell_the_conversation(
        record, f"The person confirmed the action `{action.id}` ({action.summary}): {outcome}"
    )
    await session.flush()
    return action


async def refuse(
    session: AsyncSession, record: AgentSessionRecord, action_id: str, principal: Principal
) -> ConversationAction:
    action = await _action(session, record, action_id)
    if action.status == "governed":
        raise _typed("this action is decided as a governed action in the console", "governed")
    if action.status not in ("proposed", "confirmed"):
        raise _typed(f"action {action.id} is {action.status}", f"action_{action.status}")
    action.status, action.confirmed_by, action.confirmed_at = "refused", principal.user_id, utcnow()
    await journal(session, record, "action.refused", {"action_id": action.id})
    await audit(
        session,
        principal,
        "assistant.action.refuse",
        org_id=record.org_id,
        target_type="conversation_action",
        target_id=action.id,
    )
    _tell_the_conversation(record, f"The person refused the action `{action.id}` ({action.summary}).")
    await session.flush()
    return action
