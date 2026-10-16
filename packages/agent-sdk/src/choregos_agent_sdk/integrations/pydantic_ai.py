# SPDX-License-Identifier: Apache-2.0
"""Pydantic AI toolset that calls the run's tools through the runner's permission path."""

from __future__ import annotations

from typing import Any

from ..tools import Tools
from . import need


async def toolset(run_tools: Tools) -> Any:
    """A `FunctionToolset` of the run's tools: `Agent(model, toolsets=[await pydantic_ai.toolset(...)])`."""
    ai = need("pydantic_ai", "pydantic-ai-slim")
    toolsets = need("pydantic_ai.toolsets", "pydantic-ai-slim")
    built: list[Any] = []
    for spec in await run_tools.specs():

        async def call(__name: str = spec.name, **arguments: Any) -> str:
            return str((await run_tools.call(__name, arguments)).text)

        built.append(
            ai.Tool.from_schema(
                call,
                name=spec.name,
                description=spec.description or f"The run's `{spec.name}` tool.",
                json_schema=spec.input_schema or {"type": "object", "properties": {}},
            )
        )
    return toolsets.FunctionToolset(built)
