# SPDX-License-Identifier: Apache-2.0
"""The ticket triager on the OpenAI Agents SDK, as a hosted agent (ADR 0080).

The SDK is pointed at what the platform gives every hosted container: the model at
`OPENAI_BASE_URL` (chat completions, with the loopback key), the MCP servers at
`CHOREGOS_MCP_URL<name>`. Tracing is off: it would export to OpenAI's servers, which a step's
network does not reach (the conformance suite's `no_egress` check fails otherwise).
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack

from agents import Agent, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from agents.mcp import MCPServerStreamableHttp
from openai import AsyncOpenAI

INSTRUCTIONS = (
    "You triage tickets. Use the tools to read the ticket, then answer one JSON object: "
    '{"status": "done", "summary": "<one sentence>"}.'
)

set_tracing_disabled(True)


async def answer(text: str) -> str:
    client = AsyncOpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])
    model = OpenAIChatCompletionsModel(
        model=os.environ.get("CHOREGOS_MODEL", "default"), openai_client=client
    )
    names = [name for name in os.environ.get("CHOREGOS_MCP_SERVERS", "").split(",") if name]
    async with AsyncExitStack() as stack:
        servers = [
            await stack.enter_async_context(
                MCPServerStreamableHttp(name=name, params={"url": os.environ["CHOREGOS_MCP_URL"] + name})
            )
            for name in names
        ]
        agent = Agent(name="triager", instructions=INSTRUCTIONS, model=model, mcp_servers=servers)
        result = await Runner.run(agent, text, max_turns=8)
    return str(result.final_output)
