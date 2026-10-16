# SPDX-License-Identifier: Apache-2.0
"""LangChain / LangGraph tools that call the run's tools through the runner's permission path."""

from __future__ import annotations

from typing import Any

from ..tools import Tools
from . import need


async def tools(run_tools: Tools) -> list[Any]:
    """One `StructuredTool` per tool of the run: `tools=await langchain.tools(await ctx.tools())`."""
    core = need("langchain_core.tools", "langchain-core")
    built: list[Any] = []
    for spec in await run_tools.specs():

        async def call(__name: str = spec.name, **arguments: Any) -> str:
            return str((await run_tools.call(__name, arguments)).text)

        built.append(
            core.StructuredTool(
                name=spec.name,
                description=spec.description or f"The run's `{spec.name}` tool.",
                args_schema=spec.input_schema or {"type": "object", "properties": {}},
                coroutine=call,
            )
        )
    return built
