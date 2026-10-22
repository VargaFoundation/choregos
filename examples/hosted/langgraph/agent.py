# SPDX-License-Identifier: Apache-2.0
"""The ticket triager as a LangGraph ReAct graph, as a hosted agent (ADR 0080).

`ChatOpenAI` is pointed at the platform's model (`OPENAI_BASE_URL`, the loopback key) and
`langchain-mcp-adapters` at the step's MCP servers (`CHOREGOS_MCP_URL<name>`), streamable HTTP.
"""

from __future__ import annotations

import os

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

INSTRUCTIONS = (
    "You triage tickets. Use the tools to read the ticket, then answer one JSON object: "
    '{"status": "done", "summary": "<one sentence>"}.'
)


async def answer(text: str) -> str:
    names = [name for name in os.environ.get("CHOREGOS_MCP_SERVERS", "").split(",") if name]
    client = MultiServerMCPClient(
        {
            name: {"url": os.environ["CHOREGOS_MCP_URL"] + name, "transport": "streamable_http"}
            for name in names
        }
    )
    tools = await client.get_tools()
    model = ChatOpenAI(
        model=os.environ.get("CHOREGOS_MODEL", "default"),
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )
    graph = create_react_agent(model, tools, prompt=INSTRUCTIONS)
    state = await graph.ainvoke({"messages": [("user", text)]}, {"recursion_limit": 16})
    return str(state["messages"][-1].content)
