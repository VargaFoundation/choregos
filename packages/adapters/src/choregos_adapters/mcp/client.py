# SPDX-License-Identifier: Apache-2.0
"""An MCP client, written by hand like the server (ADR 0017): the platform reaches an MCP server
that the organisation declared (`initialize`, `tools/list`, `tools/call`) over Streamable HTTP.

What it does, and why:
- `initialize` first, then `notifications/initialized`; the `Mcp-Session-Id` header the server
  returns then goes with every request, and so does `MCP-Protocol-Version`;
- an answer arrives as JSON or as an SSE stream (`text/event-stream`): the client reads the message
  that carries the request's id, and nothing else;
- `tools/list` follows `nextCursor` to the end: a server pages, and a tool on page 2 that the
  client did not see would never be seen again;
- the token is THE CONNECTOR'S, resolved by the platform: a run's token never leaves.

What it does not do: server notifications, server-to-client requests (sampling, elicitation),
resuming an interrupted stream.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import httpx
from choregos_core.mcp import VERSIONS

from ..http.egress import guarded

#: Beyond this, a server that pages without end is an error, not a list.
MAX_PAGES = 50


class McpError(RuntimeError):
    """The server answered a JSON-RPC error, or nothing readable."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    #: `annotations.readOnlyHint`: a tool that declares itself read-only. Without a declaration,
    #: it is taken for a write, the prudent default (ADR 0034).
    read_only: bool = False
    #: `outputSchema`: what the tool promises its `structuredContent` looks like; the broker holds
    #: the server to it (#755). Not part of the schema digest: it does not change what the tool does.
    output_schema: dict[str, Any] | None = None


def schema_digest(schema: dict[str, Any]) -> str:
    """The canonical digest of an input schema: a drift, even of one character, changes it."""
    canonical = json.dumps(schema or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_sse(text: str, request_id: int) -> dict[str, Any] | None:
    """The JSON-RPC message that answers `request_id` in an SSE stream; the others are ignored."""
    data: list[str] = []
    for line in [*text.splitlines(), ""]:
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line == "" and data:
            try:
                message = json.loads("\n".join(data))
            except ValueError:
                message = None
            data = []
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
    return None


class McpClient:
    def __init__(
        self,
        url: str,
        *,
        token: str = "",
        auth_scheme: str = "bearer",
        username: str = "",
        auth_header: str = "Authorization",
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = "",
    ) -> None:
        if auth_scheme not in {"bearer", "basic"}:
            raise ValueError(f"unknown MCP auth scheme `{auth_scheme}`: `bearer` or `basic`")
        if auth_scheme == "basic" and not username:
            raise ValueError("the `basic` auth scheme needs a `username` (the account's email)")
        self.url = url
        self._token = token
        # `bearer` (default): `Authorization: Bearer <token>`. `basic`: `Basic base64(username:token)`,
        # which Atlassian's Rovo MCP Server asks for when it is given an API token.
        self._auth_scheme = auth_scheme
        self._username = username
        #: The header the token travels in. `Authorization` (the default) carries `Bearer <token>`;
        #: any other name carries the raw token, which is what `X-API-Key`-style servers expect.
        self._auth_header = auth_header
        #: Sent on every request when given: who is calling, and how to reach them (ADR 0070).
        self._user_agent = user_agent
        self._timeout = timeout_s
        self._transport = transport
        self._session: str | None = None
        self._version: str = VERSIONS[0]
        self._next_id = 0
        self.server: dict[str, Any] = {}

    @property
    def serveur(self) -> dict[str, Any]:  # lang: fr-ok alias:internal
        # former name, kept for importers (ADR 0046)
        return self.server

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self._version,
        }
        if self._user_agent:
            headers["User-Agent"] = self._user_agent
        if self._token and self._auth_scheme == "basic":
            pair = base64.b64encode(f"{self._username}:{self._token}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {pair}"
        elif self._token and self._auth_header.lower() == "authorization":
            headers["Authorization"] = f"Bearer {self._token}"
        elif self._token:
            headers[self._auth_header] = self._token
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        return headers

    async def _send(self, http: httpx.AsyncClient, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        request_id = self._next_id
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            body["params"] = params
        response = await http.post(self.url, json=body, headers=self._headers())
        if response.status_code in {401, 403}:
            raise McpError(f"{method}: refused by the server ({response.status_code})", response.status_code)
        if response.status_code >= 400:
            raise McpError(f"{method}: HTTP {response.status_code}", response.status_code)
        session = response.headers.get("mcp-session-id")
        if session:
            self._session = session
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            message = _read_sse(response.text, request_id)
        else:
            try:
                message = response.json()
            except ValueError:
                message = None
        if not isinstance(message, dict):
            raise McpError(f"{method}: unreadable answer")
        if "error" in message:
            error = message["error"] or {}
            raise McpError(f"{method}: {error.get('message', 'error')}", error.get("code"))
        return message.get("result")

    async def _open(self, http: httpx.AsyncClient) -> None:
        result = await self._send(
            http,
            "initialize",
            {
                "protocolVersion": VERSIONS[0],
                "capabilities": {},
                "clientInfo": {"name": "choregos", "version": "1"},
            },
        )
        self.server = dict(result or {})
        version = self.server.get("protocolVersion")
        if isinstance(version, str):
            self._version = version
        await http.post(
            self.url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=self._headers(),
        )

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=guarded(self._transport))

    async def list_tools(self) -> list[McpTool]:
        async with self._http() as http:
            await self._open(http)
            tools: list[McpTool] = []
            cursor: str | None = None
            for _ in range(MAX_PAGES):
                result = await self._send(http, "tools/list", {"cursor": cursor} if cursor else {})
                for raw in (result or {}).get("tools", []):
                    annotations = raw.get("annotations") or {}
                    output = raw.get("outputSchema")
                    tools.append(
                        McpTool(
                            name=str(raw["name"]),
                            description=str(raw.get("description") or ""),
                            input_schema=dict(raw.get("inputSchema") or {}),
                            read_only=annotations.get("readOnlyHint") is True,
                            output_schema=dict(output) if isinstance(output, dict) else None,
                        )
                    )
                cursor = (result or {}).get("nextCursor")
                if not cursor:
                    return tools
            raise McpError(f"tools/list: more than {MAX_PAGES} pages: the server pages without end")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._http() as http:
            await self._open(http)
            result = await self._send(http, "tools/call", {"name": name, "arguments": arguments})
            return dict(result or {})

    async def test(self) -> dict[str, Any]:
        tools = await self.list_tools()
        return {"ok": True, "server": self.server.get("serverInfo"), "tools": len(tools)}


# Former names of this module, kept for the importers that other stories switch (ADR 0046, ADR 0098 §3).
ClientMcp = McpClient  # lang: fr-ok alias:internal
ErreurMcp = McpError  # lang: fr-ok alias:internal
OutilMcp = McpTool  # lang: fr-ok alias:internal
empreinte_du_schema = schema_digest  # lang: fr-ok alias:internal
