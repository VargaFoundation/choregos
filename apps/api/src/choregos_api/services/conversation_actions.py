# SPDX-License-Identifier: Apache-2.0
"""Writes proposed in a conversation, decided by the person (S63-10, ADR 0139 point 5, ADR 0030).

An assistant conversation is offered the `inline` write needs of its installation, as `<need>__<operation>`.
Calling one executes NOTHING: it stores a `ConversationAction` (`proposed`, the arguments as the agent gave
them, a readable summary) and tells the agent the action awaits the person's confirmation. The conversation's
owner confirms it (`confirm`: the broker calls the source with THE PERSON'S identity, the source staying the
judge of the right to write) or refuses it (`refuse`). Neither the agent nor the MCP door ever executes.

What is offered, and why not more:
- only a binding whose approval is `inline`. A `console` approval means a governed action (ADR 0035), which
  needs a work item a conversation has not: such a need is NOT offered, so the agent never proposes it;
  an action proposed before the owners raised the approval is refused at confirmation with `approval_console`.
- only the identity `user_exchange` (S63-07). `user_consent` (S63-08) is not offered until it can execute.
- an operation whose organisation policy is `approval` (a governed action already) is not offered either.
- no dry run: the proposal carries no diff (the optional part of the story is not done).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from choregos_core import utcnow
from choregos_core.domain import aware
from choregos_core.prompt_loop import LoopTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record as audit
from ..config import get_settings
from ..db.models import (
    AgentSessionRecord,
    AssistantToolBinding,
    ConversationAction,
    OrgConnector,
    Project,
    User,
)
from ..errors import ApiError, conflict, not_found
from ..rbac import Principal
from ..schemas.conversation_actions import ConversationAction as ConversationActionDto
from .agent_sessions import journal, tool_parameters
from .broker import (
    SEPARATOR,
    ArgumentsOutsideGrant,
    ArgumentsRefused,
    BrokerTool,
    call,
    check_arguments,
    tools_of_selection,
)
from .identity_exchange import USER_EXCHANGE, Acting

#: Identities a confirmed action can be executed under today.
EXECUTABLE_IDENTITIES = (USER_EXCHANGE,)
#: A proposal nobody decided in this time is `expired`.
EXPIRES_AFTER = timedelta(hours=24)
SUMMARY_CHARS = 300


@dataclass(frozen=True, slots=True)
class Proposable:
    """An inline write need's operation the conversation may propose."""

    name: str
    need: str
    tool: BrokerTool

    def loop_tool(self) -> LoopTool:
        description = (
            f"{self.tool.operation.description or self.tool.operation.name} - a WRITE: calling it does not "
            "run it, it proposes it to the person, who confirms or refuses it in the console"
        )
        return LoopTool(self.name, description, tool_parameters(self.tool))


def summary_of(need: str, operation: str, arguments: dict[str, Any]) -> str:
    shown = ", ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in arguments.items())
    text = f"{need}: {operation}({shown})"
    return text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 1] + "..."


async def _bound_tools(
    session: AsyncSession, record: AgentSessionRecord, project: Project, *, approval: str
) -> dict[str, Proposable]:
    """The write operations of the installation's enabled bindings with this approval, as broker tools."""
    if record.installation_id is None:
        return {}
    rows = (
        await session.execute(
            select(AssistantToolBinding, OrgConnector)
            .join(OrgConnector, OrgConnector.id == AssistantToolBinding.connector_id)
            .where(
                AssistantToolBinding.installation_id == record.installation_id,
                AssistantToolBinding.enabled.is_(True),
                AssistantToolBinding.approval == approval,
            )
        )
    ).all()
    usable = [(b, c) for b, c in rows if b.identity in EXECUTABLE_IDENTITIES]
    selection: dict[str, list[Any]] = {c.name: [str(o) for o in b.operations] for b, c in usable}
    granted = await tools_of_selection(session, selection, project)
    offered: dict[str, Proposable] = {}
    for binding, connector in usable:
        for operation in binding.operations:
            tool = granted.get(f"{connector.name}{SEPARATOR}{operation}")
            if tool is None or not tool.is_write or tool.needs_approval:
                continue
            name = f"{binding.need}{SEPARATOR}{operation}"
            offered[name] = Proposable(name, binding.need, tool)
    return offered


async def proposable(
    session: AsyncSession, record: AgentSessionRecord, project: Project
) -> dict[str, Proposable]:
    """What a turn may propose: the inline writes of the conversation's installation."""
    return await _bound_tools(session, record, project, approval="inline")


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
    action = ConversationAction(
        org_id=record.org_id,
        conversation_id=record.id,
        user_id=record.user_id or "",
        need=entry.need,
        operation=operation,
        arguments=arguments,
        summary=summary_of(entry.need, operation, arguments),
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
        status=action.status,  # type: ignore[arg-type]
        result=dict(action.result) if action.result is not None else None,
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
    return list((await session.execute(ordered)).scalars())


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
    if action.status in ("executed", "refused", "expired"):
        raise _typed(f"action {action.id} is {action.status}", f"action_{action.status}")
    if _late(action):
        action.status = "expired"
        await session.commit()
        raise _typed(f"action {action.id} expired: the assistant must propose it again", "action_expired")
    project = await session.get(Project, record.project_id) if record.project_id else None
    if project is None:
        raise _typed("the conversation has no space to act in", "no_project")
    # Read again at confirmation: the owners may have raised the approval or switched the need off since.
    inline = await _bound_tools(session, record, project, approval="inline")
    entry = next(
        (e for e in inline.values() if e.need == action.need and e.tool.operation.name == action.operation),
        None,
    )
    if entry is None:
        console = await _bound_tools(session, record, project, approval="console")
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
    user = await session.get(User, record.user_id) if record.user_id else None
    acting = Acting(
        USER_EXCHANGE,
        user_id=record.user_id or "",
        subject=(user.oidc_sub if user is not None else None) or record.user_id or "",
        org_id=record.org_id,
        actor=f"assistant:{record.agent_slug}",
    )
    action.confirmed_by, action.confirmed_at, action.status = principal.user_id, utcnow(), "confirmed"
    code, body = await call(session, entry.tool, dict(action.arguments), project=project, acting=acting)
    action.result = {"status_code": code, "body": body}
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
    outcome = "it was executed." if action.status == "executed" else f"the source answered {code}: not executed."
    _tell_the_conversation(record, f"The person confirmed the action `{action.id}` ({action.summary}): {outcome}")
    await session.flush()
    return action


async def refuse(
    session: AsyncSession, record: AgentSessionRecord, action_id: str, principal: Principal
) -> ConversationAction:
    action = await _action(session, record, action_id)
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
