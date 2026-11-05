# SPDX-License-Identifier: Apache-2.0
"""An agent case played through one door of a run's tools, on the fakes (S57-09, ADR 0122 §9).

The scripted eval matrix plays each agent case (`choregos_playbooks/evals/agent_cases/*.yaml`) in a
tools cell, through the platform's REAL doors, against a fake API and the connectors' fakes:

- `mcp`: the case's `evidence` calls go through the run's MCP server (`McpServer`, the `tools/call` a
  model makes); its `InternalClient` relays each one to the API, which records it with origin `model`;
- `scripts`: a case with a `scripted_script` runs it as an agent's shell does — a file under the run's
  temporary directory, `python3 -S`, the generated client `choregos_tools` on its `PYTHONPATH`
  (`choregos_runner.tool_scripts.write_client`) — and the script calls through the tool bridge
  (`choregos_tools_mcp.bridge`, on the tool server's app), served on the loopback by uvicorn, whose
  `InternalClient` relays to the
  API with `X-Choregos-Tool-Origin: script` and the script's digest. The client's outcome types
  (`Proposal`, `Withheld`, `ToolError`) are what the script reads. A case without a script is played
  through the MCP door in this cell too, as a model that did not use the door it was offered; the
  outcome says which (`door`: `bridge` or `mcp`).

`EvalApi` is the fake API: the run's two internal routes, `GET /runs/{id}/tools` (as `getRunTools` lists
them: description, schema, `readOnlyHint`, `x-choregos-access`) and `POST /runs/{id}/tools/{name}` (as
`callRunTool` answers: `{status_code, result, remaining, result_max_bytes}`), behind
`httpx.MockTransport`, so that both doors' real clients talk to it. It governs as `callRunTool` does for
what the cases need, and no more: the run token; the origin header (absent: `model`); arguments checked
against the operation's schema (400); a write under approval PROPOSED (202, an action
`pending_approval`, its decision path `/p/<project>/actions/<id>`, the id `uuid5(run, tool, arguments)`
as `broker.call_id` makes it); an allowed write executed as an action (`succeeded`); the injection guard
on every result (`choregos_core.injection.suspicions` over the text the agent reads: `warn` adds
`suspicions` and a security event, `block` answers 451); the 64 KiB bound on a result, its data counted
once (`broker.bounded_result`, `choregos_core.mcp.data_once`: past it, the text is cut, a
`structuredContent` it serializes dropped), named in the answer. It has no cap, no rate limit, no
masking and no network policy: the live matrix (S57-11) meets those on the real API. It cannot import
the API's own helpers: the orchestrator's imports of `choregos_api` are frozen (ADR 0099).

`mcp_calls` and `bridge_script` are the two doors with any run's `InternalClient`: the live matrix's
tests (S57-11a) play a case through them against the real API, whose connectors are fake scenes.

#1372: a case's connector may be a plugin's server, `{kind: "plugin:<plugin>/<server>", scene: <name>}`
(a plugin's matrix case, E3 and C1 of the examples page). `ready` reaches it as the broker does, over MCP
with a bearer token (`choregos_adapters.fakes.plugin_servers.plugin_scene`), on the fake registered for
it, and governs its tools by the server's declaration, found in the offline plugins: the operations it
declares only (a tool it serves without declaring is not offered), a `read` allowed, a write proposed
unless the case allows writes, `human_approval` never lowered; an executed write gets the platform's
arguments the declaration names (`action_key`, as a governed action fills it). Its answer is the
server's MCP result, as the broker relays it: an `isError` result is a 422, an MCP failure a 502.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from choregos_core import mcp

#: The doors a cell may give a run's tools (ADR 0122).
TOOLS = ("mcp", "scripts")
#: What an eval run is called on the fake API, and the project its decision paths name.
RUN_ID = "eval-run"
PROJECT = "evals"
API_BASE = "http://api.eval.invalid/internal"
#: The platform's bound on a connector's result, its data counted once (`broker_result_max_bytes`'s
#: default, `services/broker.bounded_result`).
RESULT_MAX_BYTES = mcp.RESULT_MAX_BYTES
TRUNCATED = "[truncated by the platform: {returned} of {total} bytes; ask for less]"
#: `broker.call_id`'s namespace: the same call is the same action.
CALL_NAMESPACE = uuid.UUID("6f1c4b7e-5a3d-4c2b-9e8f-0a1b2c3d4e5f")
#: How long a case's script may run.
SCRIPT_TIMEOUT_S = 120.0
ORIGINS = ("model", "script", "job")
#: The built-in tools a case may offer (`web:` in the case).
WEB_SEARCH, WEB_FETCH = "web_search", "web_fetch"


@dataclass
class Tool:
    """One tool of the eval run: a connector's operation, or a built-in."""

    name: str
    description: str
    input_schema: dict[str, Any]
    access: str
    connector: Any = None
    operation: str = ""
    #: What the platform fills on an executed write, never the agent (`OperationSpec.platform_arguments`).
    platform_arguments: tuple[str, ...] = ()


@dataclass
class EvalApi:
    """The fake internal API of one eval run (the module's docstring says what it governs)."""

    case: dict[str, Any]
    token: str = field(default_factory=lambda: f"eval-token-{uuid.uuid4().hex}")
    run_id: str = RUN_ID
    #: `{tool, origin, status_code, script_sha256}`, every call that reached the API, in order.
    calls: list[dict[str, Any]] = field(default_factory=list)
    #: `{id, tool, status, arguments}`, every governed action, once per call id.
    actions: list[dict[str, Any]] = field(default_factory=list)
    #: `{type, tool}`, the security events the guard raised.
    security_events: list[dict[str, Any]] = field(default_factory=list)
    #: name → the scene a test reads (`choregos_adapters.fakes.scenes.Scene`, or for a plugin's server
    #: `choregos_adapters.fakes.plugin_servers.PluginScene`).
    scenes: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, Tool] = field(default_factory=dict)
    #: name → `(kind, scene)` of the plugin servers `ready` has not reached yet (#1372).
    pending: dict[str, tuple[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from choregos_adapters.fakes.plugin_servers import plugin_kind
        from choregos_adapters.fakes.scenes import scene

        for name, declared in (self.case.get("connectors") or {}).items():
            kind, named = str(declared["kind"]), str(declared.get("scene", "default"))
            if plugin_kind(kind) is not None:
                # A plugin's server is listed over MCP: `ready` reaches it (#1372).
                self.pending[str(name)] = (kind, named)
                continue
            built = scene(kind, named)
            self.scenes[name] = built
            self._offer(str(name), built.connector, built.operations)
        web = self.case.get("web") or {}
        if web.get("search") is not None:
            self.tools[WEB_SEARCH] = Tool(
                WEB_SEARCH,
                "Searches the web; returns titles, links and snippets.",
                {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
                "read",
            )
        if web.get("pages") is not None:
            self.tools[WEB_FETCH] = Tool(
                WEB_FETCH,
                "Reads a web page as text.",
                {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
                "read",
            )

    def _offer(self, name: str, connector: Any, operations: Any) -> None:
        """A connector's operations as the run's tools, governed as `callRunTool` governs them."""
        writes = str((self.case.get("policy") or {}).get("writes", "approval"))
        for operation in operations:
            access = "read"
            if operation.access != "read":
                access = "approval" if operation.human_approval or writes != "allowed" else "write"
            description = operation.description or operation.name
            if access == "approval":
                description += " — needs a human approval: calling it proposes the action, it does not run it"
            elif access == "write":
                description += (
                    " — runs as a governed action: journaled, done once, undone if a later step fails"
                )
            tool = f"{name}__{operation.name}"
            self.tools[tool] = Tool(
                tool,
                f"[{name}] {description}",
                operation.input_schema or {"type": "object"},
                access,
                connector,
                operation.name,
                tuple(operation.platform_arguments),
            )

    async def ready(self) -> EvalApi:
        """The case's plugin servers reached on their fakes and their tools offered (#1372): each one's
        declaration from the offline plugins (`choregos_core.agent_catalogue`), its tools as it lists
        them. Raises `AdapterError` when no plugin ships the server or no fake of it is registered.
        Idempotent; the routes call it before they answer."""
        from choregos_adapters.fakes.plugin_servers import plugin_scene
        from choregos_core.agent_catalogue import catalogue

        while self.pending:
            name, (kind, named) = next(iter(self.pending.items()))
            built = await plugin_scene(kind, named, catalogue().server(kind))
            self.scenes[name] = built
            self._offer(name, built.connector, built.operations)
            del self.pending[name]
        return self

    # ───────────────────────── the routes ─────────────────────────

    def listed(self) -> list[dict[str, Any]]:
        """`getRunTools`' entries."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "annotations": {"readOnlyHint": tool.access == "read"},
                "x-choregos-access": tool.access,
            }
            for tool in self.tools.values()
        ]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        """The internal routes the doors call, as `httpx.MockTransport` hands them over."""
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return _problem(401, "Unauthorized", "a run token is required")
        await self.ready()
        prefix = f"/internal/runs/{self.run_id}/tools"
        path = request.url.path
        if request.method == "GET" and path == prefix:
            return httpx.Response(200, json={"tools": self.listed()})
        if request.method == "POST" and path.startswith(prefix + "/"):
            origin = request.headers.get("x-choregos-tool-origin") or "model"
            if origin not in ORIGINS:
                return _problem(
                    400, "Tool origin refused", f"X-Choregos-Tool-Origin must be one of {ORIGINS}"
                )
            try:
                arguments = json.loads(request.content or b"{}")
            except ValueError:
                return _problem(400, "Arguments refused", "the body is not JSON")
            name = unquote(path[len(prefix) + 1 :])
            return await self.call(
                name, arguments, origin=origin, script=request.headers.get("x-choregos-script")
            )
        return _problem(404, "Not Found", f"{request.method} {path} is not a route of the eval API")

    async def call(
        self, name: str, arguments: Any, *, origin: str = "model", script: str | None = None
    ) -> httpx.Response:
        """`callRunTool`, for the cases' tools."""
        import jsonschema

        await self.ready()
        tool = self.tools.get(name)
        if tool is None:
            return _problem(404, "Tool not found", f"Tool `{name}` does not exist")
        if not isinstance(arguments, dict):
            return _problem(400, "Arguments refused", "the arguments are not a JSON object")
        try:
            jsonschema.validate(arguments, tool.input_schema)
        except jsonschema.ValidationError as refused:
            return _problem(
                400, "Arguments refused", f"argument refused by the schema of {name}: {refused.message}"
            )
        suspicions: list[dict[str, str]] = []
        if tool.access == "approval":
            code, result = 202, self._propose(tool, arguments)
        else:
            code, result = await self._execute(tool, arguments)
            code, result, suspicions = self._guard(tool, code, result)
        self.calls.append({"tool": name, "origin": origin, "status_code": code, "script_sha256": script})
        body: dict[str, Any] = {"status_code": code, "result": result, "remaining": None}
        if suspicions:
            body["suspicions"] = suspicions
        # The bound the result was held to, as `callRunTool` says it (#1383): none for a proposal.
        body["result_max_bytes"] = None if tool.access == "approval" else RESULT_MAX_BYTES
        return httpx.Response(200, json=body)

    # ───────────────────────── governance ─────────────────────────

    def _action_id(self, tool: Tool, arguments: dict[str, Any]) -> str:
        canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        return str(uuid.uuid5(CALL_NAMESPACE, f"{self.run_id}:{tool.name}:{canonical}"))

    def _action(self, tool: Tool, arguments: dict[str, Any], status: str) -> dict[str, Any]:
        action_id = self._action_id(tool, arguments)
        known = next((a for a in self.actions if a["id"] == action_id), None)
        if known is None:
            known = {"id": action_id, "tool": tool.name, "status": status, "arguments": arguments}
            self.actions.append(known)
        return known

    def _propose(self, tool: Tool, arguments: dict[str, Any]) -> dict[str, Any]:
        action = self._action(tool, arguments, "pending_approval")
        return {
            "action": action["id"],
            "status": action["status"],
            "message": "proposed: a person approves it in the console before anything is done",
            "decision_path": f"/p/{PROJECT}/actions/{action['id']}",
        }

    async def _execute(self, tool: Tool, arguments: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        from choregos_adapters.errors import AdapterError

        if hasattr(tool.connector, "call_tool"):
            return await self._call_server(tool, arguments)
        try:
            if tool.name == WEB_SEARCH:
                raw: dict[str, Any] = await self._search(str(arguments["query"]))
            elif tool.name == WEB_FETCH:
                return self._fetch(str(arguments["url"]))
            else:
                raw = await tool.connector.execute(tool.operation, arguments)
        except AdapterError as refused:
            return 422, {"error": str(refused)}
        result: dict[str, Any] = {"content": [{"type": "text", "text": _json(raw)}], "structuredContent": raw}
        if tool.access == "write":
            action = self._action(tool, arguments, "succeeded")
            result["action"] = action["id"]
        return 200, _bounded(result)

    async def _call_server(self, tool: Tool, arguments: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """A plugin's server (#1372), as the broker calls one: its MCP result relayed, an `isError` result
        a 422, an MCP failure a 502; an executed write with the platform's arguments filled as a governed
        action fills them (`effects._platform_arguments`), never the agent's."""
        from choregos_adapters.mcp import McpError

        sent = dict(arguments)
        action_id = self._action_id(tool, arguments) if tool.access == "write" else ""
        for platform in tool.platform_arguments:
            sent.pop(platform, None)
        if "action_key" in tool.platform_arguments and action_id:
            canonical = json.dumps(sent, sort_keys=True, ensure_ascii=False, default=str)
            sent["action_key"] = hashlib.sha256(
                f"{action_id}:{tool.operation}:{canonical}".encode()
            ).hexdigest()[:24]
        try:
            result = await tool.connector.call_tool(tool.operation, sent)
        except (McpError, httpx.HTTPError) as failure:
            return 502, {"error": f"{tool.name}: {failure}"}
        if result.get("isError"):
            return 422, _bounded(result)
        if tool.access == "write":
            result = {**result, "action": self._action(tool, arguments, "succeeded")["id"]}
        return 200, _bounded(result)

    async def _search(self, query: str) -> dict[str, Any]:
        from choregos_adapters.fakes.scenes import web_scene

        # The case's fake web, built as the live matrix's is (#1378).
        backend = web_scene(self.case.get("web") or {}).search
        found = await backend.search(query, max_results=10)
        return {"backend": "fake", "results": [{**hit, "fetchable": True} for hit in found]}

    def _fetch(self, url: str) -> tuple[int, dict[str, Any]]:
        """`web_fetch`'s answer (`services/web_fetch.py`): the page's text in the text block, what was
        fetched in `structuredContent`."""
        page = ((self.case.get("web") or {}).get("pages") or {}).get(url)
        if page is None:
            return 404, {"error": f"{url}: not found", "type": "not_found"}
        text = str(page["text"])
        structured = {
            "url": url,
            "status": 200,
            "content_type": "text/html",
            "bytes": len(text.encode()),
            "truncated": False,
            "redirects": [],
            "title": str(page.get("title", "")),
        }
        return 200, _bounded({"content": [{"type": "text", "text": text}], "structuredContent": structured})

    def _guard(self, tool: Tool, code: int, result: Any) -> tuple[int, Any, list[dict[str, str]]]:
        """`_injection_guard`: what a third party wrote, the agent reads; `warn` says so, `block`
        withholds it."""
        from choregos_core.injection import suspicions as find_suspicions

        mode = str((self.case.get("policy") or {}).get("prompt_injection", "warn"))
        suspected = [] if mode == "ignore" else find_suspicions(_readable(result), source=f"tool.{tool.name}")
        if not suspected:
            return code, result, []
        self.security_events.append({"type": "choregos.security.injection_suspected", "tool": tool.name})
        found = [s.to_dict() for s in suspected]
        if mode == "block":
            withheld = {"error": f"result of {tool.name} withheld: suspected injection — a human reviews it"}
            return 451, withheld, found
        return code, result, found

    def observation(self, cell: str) -> dict[str, Any]:
        """What the run did, as the case judge reads it (`check_assertions`)."""
        return {
            "tools": cell,
            "requests": None,
            "calls": [{k: v for k, v in call.items() if k != "script_sha256"} for call in self.calls],
            "actions": [{"tool": a["tool"], "status": a["status"]} for a in self.actions],
            "security_events": list(self.security_events),
            "scripts": [],
        }


def _problem(status: int, title: str, detail: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": "about:blank", "title": title, "status": status, "detail": detail},
        headers={"content-type": "application/problem+json"},
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _bounded(result: dict[str, Any], limit: int = RESULT_MAX_BYTES) -> dict[str, Any]:
    """`broker.bounded_result`'s size rule: a result larger than `limit` as JSON, its data counted once
    (a text block that serializes its `structuredContent` is not counted again), keeps its text, cut,
    with the platform's marker, and loses `structuredContent` when a text block serializes it — half
    an object is not the object —; a structure the text does not say (`web_fetch`'s fetch beside its
    page) stays beside the cut text when both fit (#1383)."""
    total = len(_json(mcp.data_once(result)).encode("utf-8"))
    if total <= limit:
        return result
    text = "\n".join(str(b.get("text", "")) for b in result.get("content") or [] if b.get("type") == "text")
    kept = {k: v for k, v in result.items() if k not in ("content", "structuredContent")}
    structured = result.get("structuredContent")
    if structured is not None and not any(mcp.serializes(b, structured) for b in result.get("content") or []):
        beside = _cut(text, {**kept, "structuredContent": structured}, total, limit)
        if len(_json(beside).encode("utf-8")) <= limit:
            return beside
    return _cut(text, kept, total, limit)


def _cut(text: str, kept: dict[str, Any], total: int, limit: int) -> dict[str, Any]:
    """`kept` with `text` cut and the platform's marker, no larger than `limit` as JSON."""
    whole, room = text.encode("utf-8"), limit
    while True:
        shown = whole[:room].decode("utf-8", errors="ignore")
        returned = len(shown.encode("utf-8"))
        cut = {
            **kept,
            "content": [
                {"type": "text", "text": f"{shown}\n{TRUNCATED.format(returned=returned, total=total)}"}
            ],
            "truncated": {"returned_bytes": returned, "total_bytes": total},
        }
        over = len(_json(cut).encode("utf-8")) - limit
        if over <= 0 or room == 0:
            return cut
        room = max(0, room - over)


def _readable(payload: Any) -> str:
    """`broker.readable_text`: an MCP result's text blocks and every string of its
    `structuredContent`, otherwise every string of the JSON, keys included."""
    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        text = "\n".join(
            str(b.get("text", ""))
            for b in payload["content"]
            if isinstance(b, dict) and b.get("type") == "text"
        )
        structured = payload.get("structuredContent")
        return text if structured is None else f"{text}\n{_readable(structured)}"
    strings: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                strings.append(str(key))
                walk(item)

    walk(payload)
    return "\n".join(strings)


# ───────────────────────── the doors ─────────────────────────


@dataclass
class Played:
    """A case played in a cell: the agent's answer, what its run did, and through which door."""

    answer: str
    observation: dict[str, Any]
    door: str
    script: dict[str, Any] | None = None
    api: EvalApi | None = None


def _client(api: EvalApi) -> Any:
    from choregos_tools_mcp.client import InternalClient

    return InternalClient(API_BASE, api.run_id, api.token, transport=httpx.MockTransport(api.handle))


async def through_mcp(api: EvalApi, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The calls as a model makes them: `tools/call` on the run's MCP server. Returns its answers."""
    return await mcp_calls(_client(api), calls)


async def mcp_calls(client: Any, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`through_mcp` with any run's `InternalClient` (the live matrix's tests give it the real API's);
    the client is closed when the calls are made."""
    from choregos_tools_mcp.server import McpServer
    from choregos_tools_mcp.tools import ToolContext

    try:
        server = McpServer(client, ToolContext())
        answers: list[dict[str, Any]] = []
        for number, call in enumerate(calls, start=1):
            message = {
                "jsonrpc": "2.0",
                "id": number,
                "method": "tools/call",
                "params": {"name": str(call["tool"]), "arguments": dict(call.get("arguments") or {})},
            }
            answers.append(await server.handle(message) or {})
        return answers
    finally:
        await client.aclose()


class _Journal:
    """The bridge's journal of an eval run: in memory, in order; and, when given one, the run's own
    journal too (the sidecar's `ApiJournal`, in a live run)."""

    def __init__(self, forward: Any = None) -> None:
        self.events: list[dict[str, Any]] = []
        self.forward = forward

    async def record(self, type_: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": type_, "payload": payload})
        if self.forward is not None:
            await self.forward.record(type_, payload)


async def through_the_bridge(api: EvalApi, script: dict[str, Any], workdir: Path) -> dict[str, Any]:
    """The case's script, run as an agent's shell runs it, its calls through the real tool bridge."""
    return await bridge_script(_client(api), script, workdir, run_id=api.run_id, secrets=[api.token])


async def bridge_script(
    client: Any,
    script: dict[str, Any],
    workdir: Path,
    *,
    run_id: str,
    secrets: list[str],
    journal: Any = None,
) -> dict[str, Any]:
    """`through_the_bridge` with any run's `InternalClient`, its bridge's lines also recorded in `journal`
    when one is given (the live matrix's tests give it the real API's and the run's journal)."""
    import uvicorn
    from choregos_contracts import ToolScripts
    from choregos_runner import tool_scripts
    from choregos_tools_mcp.app import create_app
    from choregos_tools_mcp.bridge import Bridge

    lines = _Journal(journal)
    bridge = Bridge(client, ToolScripts(max_concurrent_calls=4), lines, secrets=secrets)
    # The run's tool server as the runner builds it with a bridge, its lifespan off: no MCP server of
    # its own is injected (the `mcp` door is played on `McpServer` directly), no module state is set.
    app = create_app(bridge=bridge)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    )
    serving = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started or serving.done():
                break
            await asyncio.sleep(0.05)
        if not server.started:
            raise RuntimeError("the eval's tool bridge did not start")
        port = server.servers[0].sockets[0].getsockname()[1]
        listed = await client.relay_tools()
        tools, refused = tool_scripts.tool_list(listed.status_code, listed.content)
        if refused:
            raise RuntimeError(refused)
        written = tool_scripts.write_client(
            workdir / "choregos" / "tools",
            tools,
            bridge=f"http://127.0.0.1:{port}/bridge/v1",
            run_id=run_id,
        )
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(workdir),
            **tool_scripts.agent_environment(written, {}),
            "TMPDIR": str(workdir),
        }
        path = workdir / str(script["name"])
        text = str(script["text"])
        path.write_text(text, encoding="utf-8")
        # `-S`: the standard library and the client, nothing of the orchestrator's environment.
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-S",
            str(path),
            cwd=workdir,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=SCRIPT_TIMEOUT_S)
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
        return {
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "journal": lines.events,
            "modules": written.modules,
        }
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(serving, timeout=5)
        await client.aclose()


def scripts_of(ran: dict[str, Any]) -> list[dict[str, Any]]:
    """The scripts of a run as the matrix counts them: a script is a digest the bridge journaled
    (`tools.script`) or that a relayed call names (`tools.script.call`). It is in error when one of its
    calls was answered 400 or more (the client raised `ToolError`, `CapReached` or `Withheld`), or when
    its exit code is known and is not 0. A script that never called the bridge is not counted: the
    live run would not know of it."""
    seen: dict[str, dict[str, Any]] = {}
    for event in ran.get("journal") or []:
        payload = event.get("payload") or {}
        digest = (
            payload.get("sha256") if event.get("type") == "tools.script" else payload.get("script_sha256")
        )
        if not digest:
            continue
        script = seen.setdefault(str(digest), {"sha256": str(digest), "calls": 0, "failed_calls": 0})
        if event.get("type") == "tools.script.call":
            script["calls"] += 1
            if int(payload.get("status_code") or 0) >= 400:
                script["failed_calls"] += 1
    for script in seen.values():
        exit_code = ran.get("exit_code") if script["sha256"] == ran.get("sha256") else None
        script["exit_code"] = exit_code
        script["errored"] = script["failed_calls"] > 0 or (exit_code is not None and exit_code != 0)
    return list(seen.values())


def answer_of(case: dict[str, Any], stdout: str | None) -> str:
    """The scripted agent's answer: the script's answer with what it printed in place of
    `<<stdout>>` when a script ran, the case's reference answer otherwise."""
    if stdout is None:
        return str(case.get("reference_answer", ""))
    template = (case.get("scripted_script") or {}).get("answer", "<<stdout>>")
    if isinstance(template, dict):
        filled = {key: str(value).replace("<<stdout>>", stdout.strip()) for key, value in template.items()}
        return json.dumps(filled, ensure_ascii=False)
    return str(template).replace("<<stdout>>", stdout.strip())


async def play(case: dict[str, Any], cell: str, *, workdir: Path | None = None) -> Played:
    """`case` through the door of `cell` (`mcp` or `scripts`), on fresh fakes."""
    if cell not in TOOLS:
        raise ValueError(f"unknown tools cell {cell!r}, expected one of {', '.join(TOOLS)}")
    api = await EvalApi(case).ready()
    script = case.get("scripted_script") if cell == "scripts" else None
    if script is None:
        await through_mcp(api, [c for c in case.get("evidence") or [] if "tool" in c])
        return Played(answer_of(case, None), api.observation(cell), "mcp", api=api)
    with contextlib.ExitStack() as stack:
        directory = workdir or Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="choregos-eval-doors-"))
        )
        ran = await through_the_bridge(api, script, directory)
    observation = api.observation(cell)
    observation["scripts"] = scripts_of(ran)
    summary = {key: ran[key] for key in ("sha256", "exit_code", "stdout", "stderr", "modules")}
    return Played(answer_of(case, ran["stdout"]), observation, "bridge", script=summary, api=api)


__all__ = [
    "RESULT_MAX_BYTES",
    "TOOLS",
    "EvalApi",
    "Played",
    "answer_of",
    "bridge_script",
    "mcp_calls",
    "play",
    "scripts_of",
    "through_mcp",
    "through_the_bridge",
]
