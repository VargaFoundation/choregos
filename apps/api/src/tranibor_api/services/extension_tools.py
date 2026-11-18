# SPDX-License-Identifier: Apache-2.0
"""The tools the extensions offer to a run (`choregos.extensions`), listed and checked once.

Shared by the run's tool route and by what an agent version reaches (ADR 0127, `agent_reach`).
"""

from __future__ import annotations

from typing import Any

from fastapi import status

from ..db.models import Project, Run
from ..errors import ApiError, conflict

#: The platform's own key on an entry of the run's tool list (ADR 0122): what a call does. The run's
#: tool server does not list it to the model, which reads it in the description and `readOnlyHint`.
ACCESS_KEY = "x-choregos-access"
ACCESSES = ("read", "write", "approval")


async def extension_tools(
    session: Any, run: Run, project: Project, taken: set[str]
) -> dict[str, tuple[str, dict[str, Any]]]:
    """{name: (extension, MCP tool)} of the providers declared by the extensions (`extensions.py`).

    A name already taken, by the catalogue or by another extension, is refused with a 409 rather
    than served by one of the two: a tool masked in silence is an agent that believes it calls one
    and calls the other.
    """
    from ..extensions import tool_providers

    found: dict[str, tuple[str, dict[str, Any]]] = {}
    for extension, provider in tool_providers().items():
        for tool in await provider.list_tools(session, run, project):
            tool_name = str(tool["name"])
            if tool_name in taken or tool_name in found:
                other = found[tool_name][0] if tool_name in found else "the catalogue"
                raise conflict(f"tool `{tool_name}` declared by {other} and by the plugin `{extension}`")
            extension_result_cap(extension, tool)
            extension_access(extension, tool)
            found[tool_name] = (extension, tool)
    return found


def extension_result_cap(plugin: str, tool: dict[str, Any]) -> int | None:
    """The result cap an extension's tool declares (S40-17b), as a catalogue entry does, or None.

    `result_max_bytes` beside `name` in what the extension lists: an integer of at least 1024,
    bounded by `CHOREGOS_BROKER_RESULT_CEILING_BYTES` like every cap. Anything else is an extension
    bug, refused when the tools are listed rather than read as the default.
    """
    declared = tool.get("result_max_bytes")
    if declared is None:
        return None
    if isinstance(declared, bool) or not isinstance(declared, int) or declared < 1024:
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Plugin tool refused",
            f"tool `{tool.get('name')}` of the plugin `{plugin}` declares result_max_bytes={declared!r}:"
            " an integer of at least 1024 is expected",
        )
    return declared


def extension_access(plugin: str, tool: dict[str, Any]) -> str:
    """What calling an extension's tool does (ADR 0122): the `x-choregos-access` it declares beside
    its `name` (`read`, `write`, `approval` — a call that proposes what a person decides), else `read`
    when it is annotated `readOnlyHint: true`, else `write`: the platform does not know what it does,
    and MCP's own default is not read-only. Any other declaration is an extension bug, refused when
    the tools are listed, as a bad `result_max_bytes` is."""
    declared = tool.get(ACCESS_KEY)
    if declared is not None:
        if declared not in ACCESSES:
            raise ApiError(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Plugin tool refused",
                f"tool `{tool.get('name')}` of the plugin `{plugin}` declares {ACCESS_KEY}={declared!r}:"
                f" one of {', '.join(ACCESSES)} is expected",
            )
        return str(declared)
    annotations = tool.get("annotations")
    reads = isinstance(annotations, dict) and annotations.get("readOnlyHint") is True
    return "read" if reads else "write"
