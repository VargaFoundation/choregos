# SPDX-License-Identifier: Apache-2.0
"""The deployment's tool catalogue, and the call of a tool on behalf of a run.

The provider's key lives here, in the API's environment, never in an agent's pod. The agent names a
tool; the platform builds the request, sends it, counts the call, and returns only the response.
See `choregos_core.catalogue` for the why.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from choregos_core.catalogue import (
    Catalogue,
    CatalogueTool,
    build_request,
    load_catalogue,
    validate_arguments,
)


@lru_cache(maxsize=1)
def catalogue() -> Catalogue:
    """The deployment's catalogue, read once. Absent = no tool, not an error."""
    path = os.environ.get("CHOREGOS_TOOL_CATALOG", "")
    if not path or not Path(path).is_file():
        return Catalogue()
    return load_catalogue(Path(path).read_text(encoding="utf-8"))


def clear_cache() -> None:
    catalogue.cache_clear()


def project_tools(
    allowed: list[str], groups: list[str] | None = None, *, include_writers: bool = False
) -> list[CatalogueTool]:
    """What a project may call. Empty list = nothing, and that is the default.

    A tool that may write (`CatalogueTool.may_write`) is dropped unless `include_writers` is set:
    writes reach a third party only as governed actions (ADR 0034 §7). Only the direct call uses
    `include_writers=True`, to answer the refusal with the way out.

    A deployed catalogue does not open to every project by accident: a project declares what it
    needs, as it declares its connectors.
    """
    if not allowed:
        return []
    groups = groups or []
    # TWO locks, and they do not say the same thing. The deployment says WHO may (`groups` on the
    # tool); the project says what IT uses (`tools`). A project that declares a tool reserved for
    # a group it does not belong to does not get it, otherwise the project's list would become the
    # only control, and the project's team can edit it.
    open_tools = [o for o in catalogue().tools if o.open_to(groups) and (include_writers or not o.may_write)]
    if allowed == ["*"]:
        return open_tools
    permitted = set(allowed)
    return [o for o in open_tools if o.name in permitted]


async def call(tool: CatalogueTool, arguments: dict[str, Any]) -> tuple[int, Any]:
    """Calls the provider: direct HTTP or a remote MCP server.

    Returns the code and the body, never the key. And above all: **the run's token does not leave**.
    It authenticates the agent to US; giving it to a third-party service would hand it the means to
    write into the platform. The remote one receives `credential_env`, a key that is worth
    something to it only.
    """
    # Before any call, and for BOTH paths: an announced schema must be enforced.
    validate_arguments(tool, arguments)
    if tool.mcp is not None:
        return await _call_mcp(tool, arguments)
    key = os.environ.get(tool.credential_env, "") if tool.credential_env else None
    request = build_request(tool, arguments, key)
    async with httpx.AsyncClient() as client:
        response = await client.request(
            request["method"],
            request["url"],
            params=request["params"] or None,
            headers=request["headers"] or None,
            json=request["json"],
            timeout=request["timeout"],
        )
    try:
        return response.status_code, response.json()
    except ValueError:
        # A provider that answers text is not a failure: we return it as it is, truncated, rather
        # than fail the step on a format problem.
        return response.status_code, {"text": response.text[:20_000]}


async def _call_mcp(tool: CatalogueTool, arguments: dict[str, Any]) -> tuple[int, Any]:
    """JSON-RPC `tools/call` on an MCP server outside the organisation.

    What leaves: the tool's name AT THE REMOTE, the agent's arguments, and the catalogue's headers,
    including the service's key, never ours. The agent chooses neither the URL nor the remote name:
    it names a tool of the catalogue, and the indirection is what makes it possible to expose only
    part of a server.
    """
    remote = tool.mcp
    assert remote is not None  # guaranteed by `call`
    key = os.environ.get(tool.credential_env, "") if tool.credential_env else ""
    headers = {
        name: value.replace("{{ credential }}", key).replace("{{credential}}", key)
        for name, value in remote.headers.items()
    }
    headers.setdefault("Content-Type", "application/json")
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": remote.tool, "arguments": arguments},
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(remote.url, json=body, headers=headers, timeout=remote.timeout_s)
    try:
        payload = response.json()
    except ValueError:
        return response.status_code, {"text": response.text[:20_000]}
    # A JSON-RPC error is an answer, not a transport failure: we return it as it is with a telling
    # code, so that the agent reads it instead of taking it for an empty success.
    if isinstance(payload, dict) and "error" in payload:
        return 502, payload["error"]
    return response.status_code, (payload.get("result") if isinstance(payload, dict) else payload)
