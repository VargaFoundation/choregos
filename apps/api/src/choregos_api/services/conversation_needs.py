# SPDX-License-Identifier: Apache-2.0
"""The need tools of an assistant's conversation (ADR 0133 point 2): the agent names a NEED where a plain
agent names a connector, and sees `<need>__<operation>`, never a connector's slug. The installation binds each
need to a connector of the organisation; changing the binding changes neither the instructions nor the tool
names the model reads.

A conversation without an installation, and every `mcp_servers` entry that names no bound need, go the plain
way (`agent_sessions.session_tools`). A need that is unbound or disabled offers no tool."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionRecord, AssistantToolBinding, OrgConnector, Project
from .broker import SEPARATOR, BrokerTool, selection_of_spec, tools_of_selection

if TYPE_CHECKING:
    from ..schemas import AgentSpec


async def _bindings(
    session: AsyncSession, installation_id: str
) -> dict[str, tuple[AssistantToolBinding, str]]:
    """need -> (its binding, name of the connector it is bound to)."""
    rows = await session.execute(
        select(AssistantToolBinding, OrgConnector.name)
        .join(OrgConnector, OrgConnector.id == AssistantToolBinding.connector_id)
        .where(AssistantToolBinding.installation_id == installation_id)
    )
    return {b.need: (b, connector) for b, connector in rows.all()}


async def _reached(
    session: AsyncSession, record: AgentSessionRecord, spec: AgentSpec, project: Project
) -> tuple[AgentSpec, list[tuple[str, AssistantToolBinding, list[BrokerTool]]]]:
    """The spec without the servers that name a bound need, and for each ENABLED need its binding and every
    tool (read or write) the agent's grants reach through the connector the need is bound to."""
    if not record.installation_id:
        return spec, []
    bindings = await _bindings(session, record.installation_id)
    selection = selection_of_spec(spec.model_dump(mode="json"))
    needs = {name: grants for name, grants in selection.items() if name in bindings}
    if not needs:
        return spec, []
    plain = spec.model_copy(update={"mcp_servers": [s for s in spec.mcp_servers if s.connector not in needs]})
    reached: list[tuple[str, AssistantToolBinding, list[BrokerTool]]] = []
    for need, grants in needs.items():
        binding, connector = bindings[need]
        if not binding.enabled:
            continue
        tools = await tools_of_selection(session, {connector: grants}, project)
        reached.append(
            (need, binding, [replace(t, name=f"{need}{SEPARATOR}{t.operation.name}") for t in tools.values()])
        )
    return plain, reached


async def need_tools(
    session: AsyncSession, record: AgentSessionRecord, spec: AgentSpec, project: Project
) -> tuple[AgentSpec, dict[str, BrokerTool]]:
    """The spec without the servers that name a bound need, and the read tools those needs reach."""
    plain, reached = await _reached(session, record, spec, project)
    tools = {t.name: t for _, _, found in reached for t in found if not t.is_write and not t.needs_approval}
    return plain, tools


#: What a conversation can execute once the person confirms (S63-10): the identities `acting=` supports.
EXECUTABLE_IDENTITIES = ("user_exchange",)


async def write_tools(
    session: AsyncSession, record: AgentSessionRecord, spec: AgentSpec, project: Project, *, approval: str
) -> dict[str, tuple[str, BrokerTool]]:
    """`<need>__<operation>` -> (need, tool) of the write operations of the needs whose binding carries this
    `approval` and an identity a confirmation can execute under. An operation whose organisation policy is
    `approval` is a governed action already and is left out."""
    _, reached = await _reached(session, record, spec, project)
    return {
        t.name: (need, t)
        for need, binding, found in reached
        if binding.approval == approval and binding.identity in EXECUTABLE_IDENTITIES
        for t in found
        if t.is_write and not t.needs_approval
    }


async def need_identities(session: AsyncSession, record: AgentSessionRecord) -> dict[str, str]:
    """connector name -> the identity of the BINDING (`assistant_tool_bindings.identity`, ADR 0135) of each
    enabled need, for the needs that call as the person (`user_exchange`, `user_consent`). The agent's
    entry names a need, not a connector, so the mode comes from the binding, not from the spec."""
    if not record.installation_id:
        return {}
    rows = await session.execute(
        select(OrgConnector.name, AssistantToolBinding.identity)
        .join(OrgConnector, OrgConnector.id == AssistantToolBinding.connector_id)
        .where(
            AssistantToolBinding.installation_id == record.installation_id,
            AssistantToolBinding.enabled.is_(True),
            AssistantToolBinding.identity != "service",
        )
    )
    return dict(rows.all())


async def identities_of(session: AsyncSession, record: AgentSessionRecord, spec: AgentSpec) -> dict[str, str]:
    """connector name -> identity for a conversation's tool calls: what the agent version's entries pick, and
    over it what the bindings of the installation's needs say."""
    picked = {server.connector: server.identity for server in spec.mcp_servers if server.identity}
    return {**picked, **await need_identities(session, record)}
