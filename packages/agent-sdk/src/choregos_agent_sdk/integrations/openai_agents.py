# SPDX-License-Identifier: Apache-2.0
"""OpenAI Agents SDK tools that call the run's tools through the runner's permission path."""

from __future__ import annotations

import json
from typing import Any

from ..tools import Tools
from . import need


async def tools(run_tools: Tools) -> list[Any]:
    """One `FunctionTool` per tool of the run: `Agent(tools=await openai_agents.tools(await ctx.tools()))`."""
    agents = need("agents", "openai-agents")
    built: list[Any] = []
    for spec in await run_tools.specs():

        async def invoke(_context: Any, arguments: str, __name: str = spec.name) -> str:
            return str((await run_tools.call(__name, json.loads(arguments or "{}"))).text)

        built.append(
            agents.FunctionTool(
                name=spec.name,
                description=spec.description or f"The run's `{spec.name}` tool.",
                params_json_schema=spec.input_schema or {"type": "object", "properties": {}},
                on_invoke_tool=invoke,
                strict_json_schema=False,
            )
        )
    return built
