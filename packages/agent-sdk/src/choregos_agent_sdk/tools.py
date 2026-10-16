# SPDX-License-Identifier: Apache-2.0
"""The tools of the run (MCP): `choregos-tools`, memory, the connectors the agent is granted.

The runner hands the agent its MCP servers in `session/new`. `Tools` connects to them and calls a
tool **through the runner's permission path** — the reason the SDK, not the user's code, makes the
call. Calling the servers directly would work and would skip the guard: a policy that forbids a tool
would not see it, the journal would not list it, and `max_turns` would not count it.
"""

from __future__ import annotations

import contextlib
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from .errors import ToolError
from .session import RunSession


@dataclass(frozen=True)
class ToolSpec:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    text: str
    structured: Any = None


class Tools:
    """MCP sessions on the run's servers, opened on first use and closed with the step."""

    def __init__(self, session: RunSession) -> None:
        self._session = session
        self._stack = AsyncExitStack()
        self._clients: dict[str, Any] = {}
        self._specs: dict[str, ToolSpec] | None = None
        self._connected = False

    async def _connect(self) -> None:
        if self._connected:
            return
        self._connected = True
        for server in self._session.mcp_servers:
            name = str(getattr(server, "name", ""))
            try:
                self._clients[name] = await self._open(server)
            except Exception as exc:
                # A server that is down is not the end of the step: its tools are not listed, and
                # an agent that needs one finds it missing — which is a fact it can report.
                await self._session.say(f"tool server `{name}` is unreachable: {type(exc).__name__}")

    async def _open(self, server: Any) -> Any:
        from mcp import ClientSession

        url = getattr(server, "url", None)
        if url:
            from mcp.client.streamable_http import (  # type: ignore[attr-defined]
                create_mcp_http_client,
                streamable_http_client,
            )

            headers = {h.name: h.value for h in getattr(server, "headers", [])}
            http = await self._stack.enter_async_context(create_mcp_http_client(headers=headers or None))
            streams = await self._stack.enter_async_context(streamable_http_client(url, http_client=http))
        else:
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=server.command,
                args=list(server.args),
                env={e.name: e.value for e in server.env} or None,
            )
            streams = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(streams[0], streams[1]))
        await session.initialize()
        return session

    async def specs(self) -> list[ToolSpec]:
        """Every tool the run's servers offer."""
        await self._connect()
        if self._specs is None:
            found: dict[str, ToolSpec] = {}
            for server, client in self._clients.items():
                for tool in (await client.list_tools()).tools:
                    found.setdefault(
                        tool.name,
                        ToolSpec(server, tool.name, tool.description or "", dict(tool.input_schema or {})),
                    )
            self._specs = found
        return list(self._specs.values())

    async def names(self) -> list[str]:
        return [spec.name for spec in await self.specs()]

    async def has(self, tool: str) -> bool:
        return tool in await self.names()

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """Calls `tool` on the server that offers it, after the runner's guard allowed it.

        Raises `PermissionDenied` when the guard refuses, `ToolError` when the tool is unknown or
        answers with an error, `MaxTurnsExceeded` past the step's limit.
        """
        await self.specs()
        assert self._specs is not None
        spec = self._specs.get(tool)
        if spec is None:
            raise ToolError(
                f"unknown tool `{tool}` (this run offers: {', '.join(sorted(self._specs)) or 'none'})"
            )
        args = dict(arguments or {})

        async def invoke() -> ToolResult:
            response = await self._clients[spec.server].call_tool(tool, args)
            text = "\n".join(
                getattr(block, "text", "") for block in response.content if hasattr(block, "text")
            )
            if response.is_error:
                raise ToolError(f"`{tool}` failed: {text[:500]}")
            return ToolResult(text=text, structured=getattr(response, "structured_content", None))

        return await self._session.guarded(f"{spec.server}/{tool}", "other", args, invoke)

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._stack.aclose()
