# SPDX-License-Identifier: Apache-2.0
"""The MCP servers a hosted container reaches on `127.0.0.1:4002` (ADR 0080).

One streamable-HTTP endpoint per server the step was granted: `/mcp/<name>`. The proxy forwards the
container's JSON-RPC messages to the server with the server's credentials, which never enter the
container. A server the step was not granted is not served. Every `tools/call` is judged by the run's
guard rails, as an ACP permission request is, and journaled; a refusal answers the container with a
tool error and never reaches the server.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from choregos_contracts import StageInput
from choregos_core.egress import EgressRefusedError, host_port_of
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..egress import step_policy
from ..guardrails import GuardRails
from . import Record

_FORWARDED = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id")
_RETURNED = ("content-type", "mcp-session-id")


class McpProxy:
    def __init__(
        self, stage_input: StageInput, record: Record, guards: GuardRails, *, transport: Any = None
    ) -> None:
        self.stage_input = stage_input
        self.record = record
        self.guards = guards
        self.policy = step_policy(stage_input)
        self.http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(300, connect=10))
        self.calls = 0

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/mcp/{server}", self.handle, methods=["POST", "DELETE", "GET"])])

    async def aclose(self) -> None:
        await self.http.aclose()

    async def handle(self, request: Request) -> Response:
        server = str(request.path_params["server"])
        ref = self.stage_input.tools.mcp.get(server)
        if ref is None or not ref.url:
            await self.record("hosted.mcp_refused", {"server": server[:100], "reason": "not granted"})
            return JSONResponse(
                {"error": f"no MCP server named `{server[:100]}` is granted to this step"}, 404
            )
        if request.method == "GET":
            # Server-initiated streams are not relayed: a hosted step's tools answer its requests.
            return Response(status_code=405)
        hp = host_port_of(ref.url)
        try:
            if hp is None:
                raise EgressRefusedError("", 0, "api", "network_policy", detail="not_an_http_url")
            self.policy.check(*hp)
        except EgressRefusedError as refusal:
            await self.record("hosted.mcp_refused", {"server": server, "reason": refusal.reason})
            return JSONResponse({"error": f"the server `{server}` is outside the step's network"}, 403)
        content = await request.body()
        message = _json(content)
        if message.get("method") == "tools/call":
            refused = await self._judge(server, message)
            if refused is not None:
                return refused
        headers = {name: request.headers[name] for name in _FORWARDED if name in request.headers}
        headers.update(_credential(ref.env))  # the server's credential, held by the runner only
        try:
            answer = await self.http.request(request.method, ref.url, headers=headers, content=content)
        except httpx.HTTPError as exc:
            await self.record("hosted.mcp_unavailable", {"server": server, "error": str(exc)[:300]})
            return JSONResponse({"error": f"the MCP server `{server}` did not answer"}, 502)
        kept = {k: v for k, v in answer.headers.items() if k.lower() in _RETURNED}
        return Response(answer.content, status_code=answer.status_code, headers=kept)

    async def _judge(self, server: str, message: dict[str, Any]) -> Response | None:
        params = message.get("params") or {}
        tool = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        decision = self.guards.decide(
            {"toolCall": {"kind": "other", "title": f"{server}/{tool}", "rawInput": arguments}}
        )
        await self.record("session/request_permission", {**decision.to_event(), "tool": f"{server}/{tool}"})
        self.calls += 1
        if decision.allowed:
            return None
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {
                    "content": [
                        {"type": "text", "text": f"refused by the run's guard rails: {decision.reason}"}
                    ],
                    "isError": True,
                },
            }
        )


def _credential(env: dict[str, str]) -> dict[str, str]:
    """The header a server's `env` declares, sent the way the prompt loop sends it (`prompt._auth`):
    `Authorization` carries a bearer token, any other header its value as it is."""
    for header, value in env.items():
        if header.lower() == "authorization":
            return {"authorization": f"Bearer {value.removeprefix('Bearer ').strip()}"}
        return {header: value}
    return {}


def _json(content: bytes) -> dict[str, Any]:
    try:
        message = json.loads(content or b"{}")
    except json.JSONDecodeError:
        return {}
    return message if isinstance(message, dict) else {}
