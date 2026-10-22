# SPDX-License-Identifier: Apache-2.0
"""The ticket triager as written for Foundry, run unchanged as a hosted agent (S41-19, ADR 0080).

Nothing here points at Choregos for the model or the server. `FoundryChatClient` reads
`FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_MODEL`, and `DefaultAzureCredential` finds a managed
identity (`IDENTITY_ENDPOINT`, `IDENTITY_HEADER`): on Choregos, both are the runner's loopback model.
Foundry's own hosting adapter, `ResponsesHostServer`, serves `/readiness` and `/responses` on
`$PORT` and stops on SIGTERM. The step's MCP servers are named by URL, as a deployment names them.
"""

from __future__ import annotations

import os

from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient, ResponsesHostServer
from azure.identity.aio import DefaultAzureCredential

INSTRUCTIONS = (
    "You triage tickets. Use the tools to read the ticket, then answer one JSON object: "
    '{"status": "done", "summary": "<one sentence>"}.'
)


def triager() -> Agent:
    client = FoundryChatClient(credential=DefaultAzureCredential())
    names = [name for name in os.environ.get("CHOREGOS_MCP_SERVERS", "").split(",") if name]
    tools = [MCPStreamableHTTPTool(name=name, url=os.environ["CHOREGOS_MCP_URL"] + name) for name in names]
    return client.as_agent(name="triager", instructions=INSTRUCTIONS, tools=tools)


if __name__ == "__main__":
    ResponsesHostServer(triager).run()
