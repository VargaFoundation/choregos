# SPDX-License-Identifier: Apache-2.0
"""The ticket triager on Microsoft Agent Framework, as a hosted agent (ADR 0080).

`OpenAIChatClient` is pointed at the platform's model (`OPENAI_BASE_URL`, the loopback key) and one
`MCPStreamableHTTPTool` per MCP server of the step (`CHOREGOS_MCP_URL<name>`).
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack

from agent_framework import MCPStreamableHTTPTool
from agent_framework.openai import OpenAIChatClient

INSTRUCTIONS = (
    "You triage tickets. Use the tools to read the ticket, then answer one JSON object: "
    '{"status": "done", "summary": "<one sentence>"}.'
)


async def answer(text: str) -> str:
    client = OpenAIChatClient(
        model=os.environ.get("CHOREGOS_MODEL", "default"),
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )
    names = [name for name in os.environ.get("CHOREGOS_MCP_SERVERS", "").split(",") if name]
    async with AsyncExitStack() as stack:
        tools = [
            await stack.enter_async_context(
                MCPStreamableHTTPTool(name=name, url=os.environ["CHOREGOS_MCP_URL"] + name)
            )
            for name in names
        ]
        agent = client.as_agent(name="triager", instructions=INSTRUCTIONS, tools=tools)
        response = await agent.run(text)
    return response.text
