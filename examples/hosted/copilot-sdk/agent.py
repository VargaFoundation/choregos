# SPDX-License-Identifier: Apache-2.0
"""The ticket triager on the GitHub Copilot SDK, as a hosted agent (ADR 0080).

The SDK drives the Copilot CLI runtime. In BYOK mode (`provider`), the runtime speaks chat
completions to the platform's model at `OPENAI_BASE_URL` with `OPENAI_API_KEY` (the loopback key),
without a GitHub account. It is given the step's MCP servers (`CHOREGOS_MCP_URL<name>`, HTTP) and
none of its built-in tools: a hosted triager reads, it does not edit files or run commands.
"""

from __future__ import annotations

import os
import tempfile

from copilot import CopilotClient, PermissionHandler, ToolSet

INSTRUCTIONS = (
    "You triage tickets. Use the tools to read the ticket, then answer one JSON object and nothing "
    'else: {"status": "done", "summary": "<one sentence>"}.'
)


async def answer(text: str) -> str:
    names = [name for name in os.environ.get("CHOREGOS_MCP_SERVERS", "").split(",") if name]
    model = os.environ.get("CHOREGOS_MODEL", "default")
    with tempfile.TemporaryDirectory() as home:
        client = CopilotClient(working_directory=home, base_directory=home, use_logged_in_user=False)
        await client.start()
        try:
            session = await client.create_session(
                on_permission_request=PermissionHandler.approve_all,
                model=model,
                provider={
                    "type": "openai",
                    "wire_api": "completions",
                    "base_url": os.environ["OPENAI_BASE_URL"],
                    "api_key": os.environ["OPENAI_API_KEY"],
                },
                system_message={"mode": "replace", "content": INSTRUCTIONS},
                mcp_servers={
                    name: {"type": "http", "url": os.environ["CHOREGOS_MCP_URL"] + name, "tools": ["*"]}
                    for name in names
                },
                # The MCP tools only: none of the runtime's shell, file and web tools.
                available_tools=ToolSet().add_mcp("*"),
                working_directory=home,
            )
            reply = await session.send_and_wait(text, timeout=300)
            await session.disconnect()
        finally:
            await client.stop()
    return str(getattr(reply.data, "content", "") or "") if reply else ""
