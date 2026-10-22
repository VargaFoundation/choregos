# SPDX-License-Identifier: Apache-2.0
"""The reference hosted agent: a tool-calling loop over chat completions, standard library only.

It reads what the platform gives every hosted container (ADR 0080) and nothing else: the model at
`OPENAI_BASE_URL` with `OPENAI_API_KEY`, the MCP servers at `CHOREGOS_MCP_URL<name>` for each name of
`CHOREGOS_MCP_SERVERS`. The same image runs anywhere these variables point to a model and to
streamable-HTTP MCP servers.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

INSTRUCTIONS = (
    "You triage tickets. Use the tools to read the ticket, then answer one JSON object: "
    '{"status": "done", "summary": "<one sentence>"}.'
)
MAX_STEPS = 8
# Loopback addresses: never through a proxy, whatever the environment says.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    request = urllib.request.Request(  # noqa: S310 - the URLs come from the platform's variables
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    with _OPENER.open(request, timeout=300) as answer:
        raw = answer.read()
        kept = {k.lower(): v for k, v in answer.headers.items()}
    if kept.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in raw.decode().splitlines() if line.startswith("data:")]
        raw = data[-1].encode() if data else b"{}"
    return (json.loads(raw) if raw else {}), kept


class McpServer:
    """One streamable-HTTP MCP server, spoken to in JSON-RPC."""

    def __init__(self, name: str, url: str) -> None:
        self.name, self.url = name, url
        self.headers = {"accept": "application/json, text/event-stream"}
        self.ids = 0

    def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.ids += 1
        answer, headers = _post(
            self.url, {"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params}, self.headers
        )
        if "mcp-session-id" in headers:
            self.headers["mcp-session-id"] = headers["mcp-session-id"]
        if "error" in answer:
            raise RuntimeError(f"{self.name}: {answer['error']}")
        result: dict[str, Any] = answer.get("result") or {}
        return result

    def tools(self) -> list[dict[str, Any]]:
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "choregos-reference-agent", "version": "1"},
            },
        )
        _post(self.url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, self.headers)
        return list(self.rpc("tools/list", {}).get("tools") or [])

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        result = self.rpc("tools/call", {"name": tool, "arguments": arguments})
        return "\n".join(str(part.get("text") or "") for part in result.get("content") or [])


def answer(text: str) -> str:
    base = os.environ["OPENAI_BASE_URL"].rstrip("/")
    headers = {"authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}
    model = os.environ.get("CHOREGOS_MODEL", "default")
    servers = [
        McpServer(name, os.environ["CHOREGOS_MCP_URL"] + name)
        for name in os.environ.get("CHOREGOS_MCP_SERVERS", "").split(",")
        if name
    ]
    owners: dict[str, McpServer] = {}
    tools: list[dict[str, Any]] = []
    for server in servers:
        for tool in server.tools():
            owners[tool["name"]] = server
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description") or "",
                        "parameters": tool.get("inputSchema") or {"type": "object"},
                    },
                }
            )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": INSTRUCTIONS},
        {"role": "user", "content": text},
    ]
    for _ in range(MAX_STEPS):
        body: dict[str, Any] = {"model": model, "messages": messages, **({"tools": tools} if tools else {})}
        try:
            completion, _ = _post(f"{base}/chat/completions", body, headers)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"the model answered {exc.code}: {exc.read().decode()[:300]}") from exc
        message = completion["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            return str(message.get("content") or "")
        messages.append(message)
        for call in calls:
            function = call["function"]
            server = owners.get(function["name"])
            output = (
                server.call(function["name"], json.loads(function.get("arguments") or "{}"))
                if server
                else f"no tool named {function['name']}"
            )
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
    raise RuntimeError(f"no answer after {MAX_STEPS} model calls")
