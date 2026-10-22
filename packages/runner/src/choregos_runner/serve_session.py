# SPDX-License-Identifier: Apache-2.0
"""`choregos-runner serve-session`: the runner of a hosted session's pod (S41-11, ADR 0080 §10).

A session's pod is a step's pod that answers many turns: the agent's container beside the runner,
reaching the platform only through the runner's loopback listeners — the model (the session's key,
`max_turns` calls per turn), the session's tools on `/mcp/choregos` (judged by the guard rails, then
through the internal API's courtier) and the CONNECT proxy (the session's network). The runner takes
each turn the person asked for from `/internal/agent-sessions/{id}/turns/next`, sends it to the
container over Foundry's contract, and posts how the container answered. It stops when the API says
the session ended (410) or on SIGTERM; the pod's workflow fails whatever turn was left waiting.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from choregos_contracts import StageInput
from choregos_core.hosted import CONNECT_PORT, MCP_PORT, MODEL_PORT
from choregos_core.mcp import METHODE_INCONNUE, erreur, negocier, ok, resultat_texte
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .config import RunnerSettings
from .egress import authenticate_proxy_env
from .exits import Exit
from .guardrails import GuardRails
from .hosted import HostedStep, base_url, call_invocations, call_responses, wait_ready
from .loopback import Loopback
from .loopback.connect import ConnectProxy, upstream_proxy
from .loopback.model import ModelProxy
from .runner import with_gateway_key

#: The run id of a session's pod: `session-{id}` (`agent_hosted_sessions.session_run_id`).
SESSION_PREFIX = "session-"
#: The MCP server the runner serves the container: the session's tools.
SESSION_MCP = "choregos"
#: The events one turn reports with its answer; the API keeps no more.
MAX_EVENTS = 200
_EXCERPT = 2000


class SessionEndedError(RuntimeError):
    """The API answered 410: the session is closed, the pod has nothing more to serve."""


class SessionApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"internal API: {status_code} — {detail}")
        self.status_code = status_code


class SessionClient:
    """The pod's calls to `/internal/agent-sessions/{id}`, with the token minted for the session."""

    def __init__(
        self, api_url: str, session_id: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.base = f"{api_url.rstrip('/')}/agent-sessions/{session_id}"
        # `trust_env=False` is not set: the API is reached the way a step's runner reaches it.
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(60, connect=10),
            headers={"Authorization": f"Bearer {token}"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, suffix: str, **kwargs: Any) -> httpx.Response:
        answer = await self._http.request(method, f"{self.base}{suffix}", **kwargs)
        if answer.status_code == 410:
            raise SessionEndedError(answer.text[:400])
        if answer.status_code >= 400:
            raise SessionApiError(answer.status_code, answer.text[:400])
        return answer

    async def fetch_input(self) -> StageInput:
        return StageInput.model_validate((await self._request("GET", "/input")).json())

    async def next_turn(self, wait: float) -> dict[str, Any] | None:
        """The oldest turn waiting, or `None` when none came within `wait` seconds."""
        answer = await self._request("GET", "/turns/next", params={"wait": wait}, timeout=wait + 30)
        return None if answer.status_code == 204 else dict(answer.json())

    async def post_turn(self, turn_id: str, result: dict[str, Any]) -> None:
        await self._request("POST", f"/turns/{turn_id}", json=result)

    async def list_tools(self) -> list[dict[str, Any]]:
        return list((await self._request("GET", "/tools")).json().get("tools") or [])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        answer = await self._request("POST", f"/tools/{name}", json={"arguments": arguments})
        return str(answer.json().get("text") or "")


Record = Callable[[str, dict[str, Any]], Awaitable[None]]


class SessionToolsMcp:
    """The session's tools on the loopback MCP listener, `/mcp/choregos`: a stateless MCP server
    whose `tools/list` and `tools/call` go through the internal API. Each call is judged by the guard
    rails first, as a step's MCP calls are; a refusal never reaches the API."""

    def __init__(self, client: SessionClient, guards: GuardRails, record: Record) -> None:
        self.client = client
        self.guards = guards
        self.record = record
        self.calls = 0

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/mcp/{server}", self.handle, methods=["POST", "GET", "DELETE"])])

    async def aclose(self) -> None:
        return None

    async def handle(self, request: Request) -> Response:
        server = str(request.path_params["server"])
        if server != SESSION_MCP:
            await self.record("hosted.mcp_refused", {"server": server[:100], "reason": "not granted"})
            return JSONResponse({"error": f"no MCP server named `{server[:100]}` serves this session"}, 404)
        if request.method != "POST":
            return Response(status_code=405)
        try:
            message = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError:
            message = None
        if not isinstance(message, dict):
            return JSONResponse(erreur(None, -32700, "the body is not a JSON-RPC message"), 400)
        if "id" not in message:  # a notification: nothing to answer
            return Response(status_code=202)
        return JSONResponse(await self._answer(message))

    async def _answer(self, message: dict[str, Any]) -> dict[str, Any]:
        id_, method = message.get("id"), str(message.get("method") or "")
        params = message.get("params") or {}
        if method == "initialize":
            return ok(
                id_,
                {
                    "protocolVersion": negocier(params.get("protocolVersion")),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "choregos-session", "version": "1"},
                },
            )
        if method == "ping":
            return ok(id_, {})
        if method == "tools/list":
            return ok(id_, {"tools": await self.client.list_tools()})
        if method == "tools/call":
            return ok(id_, await self._call(str(params.get("name") or ""), params.get("arguments") or {}))
        return erreur(id_, METHODE_INCONNUE, f"`{method[:100]}` is not served")

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        decision = self.guards.decide(
            {"toolCall": {"kind": "other", "title": f"{SESSION_MCP}/{tool}", "rawInput": arguments}}
        )
        await self.record(
            "session/request_permission", {**decision.to_event(), "tool": f"{SESSION_MCP}/{tool}"}
        )
        self.calls += 1
        if not decision.allowed:
            return resultat_texte(f"refused by the session's guard rails: {decision.reason}", en_erreur=True)
        try:
            return resultat_texte(await self.client.call_tool(tool, arguments))
        except SessionApiError as refusal:
            if refusal.status_code == 404:
                return resultat_texte(
                    f"no tool named `{tool[:100]}` is granted to this session", en_erreur=True
                )
            raise


class TurnEvents:
    """What the runner saw during the turn being answered; posted with its answer."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def record(self, type_: str, payload: dict[str, Any]) -> None:
        if len(self.events) < MAX_EVENTS:
            self.events.append({"type": type_[:64], "payload": payload})

    def take(self) -> list[dict[str, Any]]:
        events, self.events = self.events, []
        return events


class SessionServer:
    """Serves one session's turns until the session ends."""

    def __init__(self, settings: RunnerSettings, *, client: SessionClient | None = None) -> None:
        self.settings = settings
        self.session_id = settings.run_id.removeprefix(SESSION_PREFIX)
        self.client = client
        self.stopping = asyncio.Event()
        #: How long one `turns/next` waits for a turn.
        self.poll_s = 20.0
        #: For tests only, as `Runner`'s: the transports, the listeners' ports, the readiness interval.
        self.api_transport: Any = None
        self.hosted_transport: Any = None
        self.model_transport: Any = None
        self.loopback_ports: tuple[int, int, int] = (MODEL_PORT, MCP_PORT, CONNECT_PORT)
        self.readiness_interval = 1.0

    async def serve(self) -> Exit:
        if not self.settings.run_id.startswith(SESSION_PREFIX):
            return Exit.INPUT_NOT_FOUND
        os.environ.update(authenticate_proxy_env(os.environ, self.settings.run_id, self.settings.run_token))
        client = self.client or SessionClient(
            self.settings.api_url, self.session_id, self.settings.run_token, transport=self.api_transport
        )
        try:
            try:
                stage_input = await client.fetch_input()
            except SessionEndedError:
                return Exit.OK
            except (SessionApiError, httpx.HTTPError):
                return Exit.INPUT_NOT_FOUND
            return await self._serve(client, with_gateway_key(stage_input, self.settings.gateway_key))
        finally:
            if self.client is None:
                await client.aclose()

    def stop(self) -> None:
        self.stopping.set()

    async def _serve(self, client: SessionClient, stage_input: StageInput) -> Exit:
        assert stage_input.hosted is not None, "a session's pod serves a hosted agent"
        turn_events = TurnEvents()
        guards = GuardRails(stage_input.permissions, [], workspace=str(self.settings.workspace))
        model = ModelProxy(stage_input, turn_events.record, transport=self.model_transport)
        tools = SessionToolsMcp(client, guards, turn_events.record)
        connect = ConnectProxy(stage_input, turn_events.record, upstream=upstream_proxy(os.environ))
        base = base_url(stage_input)
        hosted = stage_input.hosted
        try:
            async with (
                Loopback(model, tools, connect, ports=self.loopback_ports),
                httpx.AsyncClient(
                    transport=self.hosted_transport, timeout=httpx.Timeout(None, connect=10), trust_env=False
                ) as http,
            ):
                ready = await wait_ready(http, base, hosted.readiness_timeout_s, self.readiness_interval)
                while not self.stopping.is_set():
                    try:
                        turn = await self._next(client)
                    except SessionEndedError:
                        return Exit.OK
                    except (SessionApiError, httpx.HTTPError):
                        await asyncio.sleep(1)  # the API is restarting: the turn waits for us
                        continue
                    if turn is None:
                        continue
                    # A container that was not ready yet may have become so since.
                    ready = ready or await wait_ready(http, base, 0, self.readiness_interval)
                    model.turns, model.refused_turns, model.budget_refusal = 0, 0, None
                    tools.calls = 0
                    turn_events.take()
                    answer = await self._answer(http, base, stage_input, turn, ready, model)
                    answer.update(turns=model.turns, tool_calls=tools.calls, events=turn_events.take())
                    try:
                        await client.post_turn(str(turn["turn_id"]), answer)
                    except SessionEndedError:
                        return Exit.OK
        except OSError:
            return Exit.AGENT_UNREACHABLE
        return Exit.OK

    async def _next(self, client: SessionClient) -> dict[str, Any] | None:
        """The next turn, or `None` when none came or the runner is stopping."""
        poll = asyncio.ensure_future(client.next_turn(self.poll_s))
        stop = asyncio.ensure_future(self.stopping.wait())
        done, _ = await asyncio.wait({poll, stop}, return_when=asyncio.FIRST_COMPLETED)
        stop.cancel()
        if poll not in done:
            poll.cancel()
            with contextlib.suppress(BaseException):
                await poll
            return None
        return poll.result()

    async def _answer(
        self,
        http: httpx.AsyncClient,
        base: str,
        stage_input: StageInput,
        turn: dict[str, Any],
        ready: bool,
        model: ModelProxy,
    ) -> dict[str, Any]:
        """How the container answered the turn, as `AgentSessionTurnResult`."""
        hosted = stage_input.hosted
        assert hosted is not None
        if not ready:
            return {
                "status": "not_ready",
                "error": f"the container was not ready after {hosted.readiness_timeout_s} s",
            }
        call = call_invocations if turn.get("protocol") == "invocations" else call_responses
        timeout_s = stage_input.budget.max_minutes * 60
        try:
            step = await asyncio.wait_for(
                call(http, base, stage_input, str(turn.get("input") or "")), timeout_s
            )
        except TimeoutError:
            step = HostedStep("timeout", error=f"the container did not answer within {timeout_s} s")
        except (httpx.HTTPError, ValueError) as exc:
            step = HostedStep("failed", error=f"the container's answer could not be read: {str(exc)[:300]}")
        status: str = step.status
        error = step.error
        if model.budget_refusal:
            status, error = (
                "budget_exceeded",
                f"the gateway refused the session's key: {model.budget_refusal}",
            )
        elif step.status == "failed" and model.refused_turns:
            status = "max_turns"
            error = error or f"the turn used its {stage_input.budget.max_turns} model calls"
        return {
            "status": status,
            "text": step.text[:200_000],
            "error": (error or "")[:4000] or None,
            "response_id": step.response_id,
        }


async def serve_session(settings: RunnerSettings) -> Exit:
    """Serves the session until it ends; SIGTERM stops it between two turns."""
    server = SessionServer(settings)
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError, RuntimeError):
        loop.add_signal_handler(signal.SIGTERM, server.stop)
    return await server.serve()


__all__ = ["SessionClient", "SessionEndedError", "SessionServer", "SessionToolsMcp", "serve_session"]
