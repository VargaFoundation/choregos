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
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from tranibor_contracts import StageInput
from tranibor_core.egress import EgressRefusedError, host_port_of

from ..egress import step_policy
from ..guardrails import GuardRails
from . import Record

_FORWARDED = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id")
_RETURNED = ("content-type", "mcp-session-id")


class McpProxy:
    def __init__(
        self,
        stage_input: StageInput,
        record: Record,
        guards: GuardRails,
        *,
        transport: Any = None,
        verbatim: bool = False,
    ) -> None:
        """`verbatim=True`: the agent of an ACP step (ADR 0146 §7, S65-04f). The server's headers go out
        exactly as the step declared them, as the agent would have sent them itself. The listener still
        judges every `tools/call`: that is what a `curl` from the agent's shell cannot go around."""
        self.verbatim = verbatim
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
        if ref.scoped:
            # A server the runner scopes (the memory's tenant, its tool list) is reached by single JSON-RPC
            # requests only: a batch would carry `tools/call`s past the checks below.
            if _is_batch(content):
                await self.record("hosted.mcp_refused", {"server": server, "reason": "batch"})
                return JSONResponse({"error": "a JSON-RPC batch is not served for this server"}, 400)
            refused = await self._scope(server, ref, message)
            if refused is not None:
                return refused
            if message.get("method") == "tools/call":
                content = _with_scope_arguments(message, ref.scope_arguments)
        if message.get("method") == "tools/call":
            refused = await self._judge(server, message)
            if refused is not None:
                return refused
        headers = {name: request.headers[name] for name in _FORWARDED if name in request.headers}
        headers.update(dict(ref.env) if self.verbatim else _credential(ref.env))  # held by the runner only
        try:
            answer = await self.http.request(request.method, ref.url, headers=headers, content=content)
        except httpx.HTTPError as exc:
            await self.record("hosted.mcp_unavailable", {"server": server, "error": str(exc)[:300]})
            return JSONResponse({"error": f"the MCP server `{server}` did not answer"}, 502)
        kept = {k: v for k, v in answer.headers.items() if k.lower() in _RETURNED}
        body = answer.content
        if ref.allowed_tools is not None and message.get("method") == "tools/list":
            body = _only_tools(body, ref.allowed_tools)
        return Response(body, status_code=answer.status_code, headers=kept)

    async def _scope(self, server: str, ref: Any, message: dict[str, Any]) -> Response | None:
        """The server's tool list, enforced: a `tools/call` of a tool outside `allowed_tools` is answered
        with a tool error and never reaches the server (journaled, as a guard-rail refusal is)."""
        if ref.allowed_tools is None or message.get("method") != "tools/call":
            return None
        tool = str((message.get("params") or {}).get("name") or "")
        if tool in ref.allowed_tools:
            return None
        await self.record(
            "hosted.mcp_refused", {"server": server, "tool": tool[:100], "reason": "tool_not_granted"}
        )
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"the tool `{tool[:100]}` is not granted here (server `{server}`)",
                        }
                    ],
                    "isError": True,
                },
            }
        )

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
    """The headers a server's `env` declares, sent the way the prompt loop sends them (`prompt._auth`):
    `Authorization` carries a bearer token, any other header its value as it is (the memory's
    `X-Ecphoria-Tenant` rides with its key)."""
    headers: dict[str, str] = {}
    for header, value in env.items():
        if header.lower() == "authorization":
            headers["authorization"] = f"Bearer {value.removeprefix('Bearer ').strip()}"
        else:
            headers[header] = value
    return headers


def _is_batch(content: bytes) -> bool:
    try:
        return isinstance(json.loads(content or b"{}"), list)
    except json.JSONDecodeError:
        return False


def _with_scope_arguments(message: dict[str, Any], scope: dict[str, str]) -> bytes:
    """The `tools/call` with the server's scope arguments set over the agent's: what the agent wrote for
    `tenant_id` is gone, the run's project is there."""
    params = dict(message.get("params") or {})
    arguments = params.get("arguments")
    arguments = dict(arguments) if isinstance(arguments, dict) else {}
    arguments.update(scope)
    return json.dumps({**message, "params": {**params, "arguments": arguments}}).encode()


def _only_tools(body: bytes, allowed: list[str]) -> bytes:
    """A `tools/list` answer cut to the granted tools; anything that is not that JSON is left as it is (the
    calls are refused anyway)."""
    try:
        answer = json.loads(body)
        tools = answer["result"]["tools"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return body
    if not isinstance(tools, list):
        return body
    answer["result"]["tools"] = [t for t in tools if isinstance(t, dict) and t.get("name") in allowed]
    return json.dumps(answer).encode()


def _json(content: bytes) -> dict[str, Any]:
    try:
        message = json.loads(content or b"{}")
    except json.JSONDecodeError:
        return {}
    return message if isinstance(message, dict) else {}
