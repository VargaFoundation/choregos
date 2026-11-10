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


async def _bound(session: AsyncSession, installation_id: str) -> dict[str, tuple[bool, str]]:
    """need -> (enabled, name of the connector it is bound to)."""
    rows = await session.execute(
        select(AssistantToolBinding.need, AssistantToolBinding.enabled, OrgConnector.name)
        .join(OrgConnector, OrgConnector.id == AssistantToolBinding.connector_id)
        .where(AssistantToolBinding.installation_id == installation_id)
    )
    return {need: (enabled, connector) for need, enabled, connector in rows.all()}


async def need_tools(
    session: AsyncSession, record: AgentSessionRecord, spec: AgentSpec, project: Project
) -> tuple[AgentSpec, dict[str, BrokerTool]]:
    """The spec without the servers that name a bound need, and the read tools those needs reach."""
    if not record.installation_id:
        return spec, {}
    bound = await _bound(session, record.installation_id)
    selection = selection_of_spec(spec.model_dump(mode="json"))
    needs = {name: grants for name, grants in selection.items() if name in bound}
    if not needs:
        return spec, {}
    plain = spec.model_copy(update={"mcp_servers": [s for s in spec.mcp_servers if s.connector not in needs]})
    tools: dict[str, BrokerTool] = {}
    for need, grants in needs.items():
        enabled, connector = bound[need]
        if not enabled:
            continue
        reached = await tools_of_selection(session, {connector: grants}, project)
        for tool in reached.values():
            if tool.is_write or tool.needs_approval:
                continue
            name = f"{need}{SEPARATOR}{tool.operation.name}"
            tools[name] = replace(tool, name=name)
    return plain, tools
