# SPDX-License-Identifier: Apache-2.0
"""What an assistant's conversation offers beyond its need tools (ADR 0136, ADR 0138): the knowledge of
its installation (S63-05) and its memory spaces (S63-09). Gathered here so that a session's turn
(`agent_sessions.respond`) asks one thing: which extra tools, and who serves a call to one of them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from choregos_core.prompt_loop import LoopTool
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionRecord


@dataclass
class ConversationExtras:
    """The extra tools of one turn and the bases and spaces behind them."""

    record: AgentSessionRecord
    knowledge: list[Any] = field(default_factory=list)
    memory: dict[str, LoopTool] = field(default_factory=dict)
    #: The inline writes the conversation may PROPOSE (S63-10): name -> `conversation_actions.Proposable`.
    actions: dict[str, Any] = field(default_factory=dict)
    #: `run_task` (S63-13), when the installation has on-demand tasks.
    tasks: dict[str, LoopTool] = field(default_factory=dict)

    @property
    def loop_tools(self) -> list[LoopTool]:
        from .conversation_knowledge import tools_for

        return (
            tools_for(self.knowledge)
            + list(self.memory.values())
            + [a.loop_tool() for a in self.actions.values()]
            + list(self.tasks.values())
        )

    def serves(self, name: str) -> bool:
        from .conversation_knowledge import KNOWLEDGE_TOOLS

        return (
            (bool(self.knowledge) and name in KNOWLEDGE_TOOLS)
            or name in self.memory
            or name in self.actions
            or name in self.tasks
        )

    async def call(self, session: AsyncSession, name: str, arguments: dict[str, Any]) -> str:
        if name in self.tasks:
            from .conversation_tasks import call as run_task

            return await run_task(session, self.record, arguments)
        if name in self.actions:
            from .conversation_actions import propose

            return await propose(session, self.record, self.actions, name, arguments)
        if name in self.memory:
            from .assistant_memory import call_tool as call_memory_tool

            return await call_memory_tool(session, self.record, name, arguments)
        from .conversation_knowledge import serve

        return await serve(session, self.knowledge, name, arguments)


async def conversation_extras(
    session: AsyncSession, record: AgentSessionRecord, messages: list[dict[str, Any]]
) -> ConversationExtras:
    """The turn's extra tools; at the start of a conversation, what is remembered is injected as data, and
    a conversation that reads knowledge is told to cite it. Outside an assistant's conversation: none."""
    from .assistant_memory import conversation_spaces, injection_text, tool_definitions
    from .conversation_knowledge import installation_bases, with_citation_rule

    extras = ConversationExtras(record)
    from .conversation_tasks import offered

    extras.tasks = await offered(session, record)
    # S63-05: an assistant's conversation reads the knowledge of its installation, and of no other.
    extras.knowledge = list(await installation_bases(session, record))
    if extras.knowledge:
        with_citation_rule(messages)
    spaces = await conversation_spaces(session, record)
    if spaces is not None:
        defs = tool_definitions(spaces)
        extras.memory = {d["name"]: LoopTool(d["name"], d["description"], d["parameters"]) for d in defs}
    if extras.memory and not any(m.get("role") == "user" for m in messages):
        text = await injection_text(session, record)
        if text:
            at = 1 if messages and messages[0].get("role") == "system" else 0
            messages.insert(at, {"role": "system", "content": text})
    return extras
