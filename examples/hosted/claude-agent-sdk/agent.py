# SPDX-License-Identifier: Apache-2.0
"""The ticket triager on the Claude Agent SDK, as a hosted agent (ADR 0080).

The SDK drives the Claude Code CLI it bundles. The CLI reads the platform's model from
`ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` (the loopback key) and is given the step's MCP servers
(`CHOREGOS_MCP_URL<name>`, HTTP) and none of its built-in tools: a hosted triager reads, it does not
edit files or run commands. The CLI's own traffic (telemetry, error reports, updates) is turned
off: a step's network does not reach it (the conformance suite's `no_egress` check fails otherwise).
"""

from __future__ import annotations

import os
import tempfile

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

INSTRUCTIONS = (
    "You triage tickets. Use the tools to read the ticket, then answer one JSON object and nothing "
    'else: {"status": "done", "summary": "<one sentence>"}.'
)
QUIET = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_AUTOUPDATER": "1",
}


async def answer(text: str) -> str:
    names = [name for name in os.environ.get("CHOREGOS_MCP_SERVERS", "").split(",") if name]
    with tempfile.TemporaryDirectory() as home:
        options = ClaudeAgentOptions(
            system_prompt=INSTRUCTIONS,
            model=os.environ.get("CHOREGOS_MODEL"),
            tools=[],
            mcp_servers={
                name: {"type": "http", "url": os.environ["CHOREGOS_MCP_URL"] + name} for name in names
            },
            strict_mcp_config=True,
            allowed_tools=[f"mcp__{name}" for name in names],
            setting_sources=[],
            max_turns=8,
            cwd=home,
            # The CLI keeps its state in a directory of its own, never in the image's home.
            env={**QUIET, "CLAUDE_CONFIG_DIR": home},
        )
        final = ""
        async for message in query(prompt=text, options=options):
            if isinstance(message, ResultMessage):
                if message.is_error:
                    raise RuntimeError(f"the agent stopped: {message.subtype}: {message.result}")
                final = message.result or ""
    return final
