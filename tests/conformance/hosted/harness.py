"""The conformance checks of a hosted agent (ADR 0080), against the runner's own loopback listeners.

A hosted agent is a container that serves Foundry's contract and reaches the platform only through
the runner. The suite starts the agent (a process, or an image through `docker run`) with the very
environment the executor gives it (`sidecar_env`), serves the runner's model, MCP and CONNECT
listeners, sends it a triage task the way the runner does (`run_hosted`), and observes:

| check | what is observed |
| --- | --- |
| readiness | `GET /readiness` answers 200 within `readiness_timeout_s` |
| answer | the declared protocol (`/responses` streamed, or `/invocations`) answers the task |
| model_at_loopback | its model calls went through the loopback, with the run's key only, and none elsewhere |
| mcp_at_loopback | it called the granted MCP tool through the loopback, with the runner's credential |
| result_valid | the answer is a valid step result, built from the tool's answer |
| max_turns | with one model call allowed, the gateway saw one, and the agent still ended the step |
| no_egress | the task needs no internet: the agent asked the CONNECT proxy for no tunnel |
| sigterm | on SIGTERM the agent exits within `termination_grace_s` |

The gateway is scripted the same way for every framework: offered a tool named `get_ticket`, it asks
for it; given the tool's answer, it answers a JSON result that quotes it; offered no tool, it answers
without one (and `mcp_at_loopback` fails). It speaks chat completions, the Responses API and
Anthropic messages, streamed or not.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from choregos_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    HostedCall,
    McpServerRef,
    ModelRef,
    Permissions,
    PlaybookRef,
    ProjectRef,
    StageInput,
    ToolsRef,
    TransitionRef,
    WorkItemRef,
)
from choregos_core.hosted import sidecar_env
from choregos_runner.guardrails import GuardRails
from choregos_runner.hosted import result_of, run_hosted
from choregos_runner.loopback import Loopback
from choregos_runner.loopback.connect import ConnectProxy
from choregos_runner.loopback.mcp import McpProxy
from choregos_runner.loopback.model import ModelProxy

from ..backends.test_acp_conformance import ConformanceResult

HOSTED_CHECKS = (
    "readiness",
    "answer",
    "model_at_loopback",
    "mcp_at_loopback",
    "result_valid",
    "max_turns",
    "no_egress",
    "sigterm",
)
#: Built at run time: a literal that looks like a key would trip the secrets scan of the CI.
RUN_KEY = "-".join(["sk", "run", "c0f5" * 4])
TICKETS_TOKEN = "-".join(["tickets", "51e7" * 4])
#: What the ticket says; the result must quote it, so the tool's answer went back to the model.
TICKET_FACT = "priority P1"
TOOL = "get_ticket"
IMAGE = "registry.example/conformance/hosted@sha256:" + "0" * 64
ROOT = Path(__file__).resolve().parents[3]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def published_port() -> int:
    """A port Docker Desktop can publish: on Windows it refuses the ephemeral ranges the OS keeps
    for itself (`bind: … forbidden by its access permissions`), where `free_port` likes to land."""
    for _ in range(50):
        port = random.randint(10000, 29999)
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("no free port in 10000-29999")


@contextmanager
def serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# --- the gateway ------------------------------------------------------------------------------------


@dataclass
class GatewayCall:
    path: str
    key: str
    model: str
    stream: bool


@dataclass
class FakeGateway:
    url: str = ""
    calls: list[GatewayCall] = field(default_factory=list)


def _decide(tools: list[str], answered: str | None) -> tuple[str, str | None]:
    """`("tool", name)` or `("text", result)`: the scripted model, the same for every API."""
    if answered is not None:
        result = {"status": "done", "summary": f"Triaged ACME-1 from the ticket: {answered[:200]}"}
        return "text", json.dumps(result)
    # Each SDK prefixes an MCP tool with its server its own way: `mcp__tickets__get_ticket` (Claude),
    # `tickets-get_ticket` (Copilot), or not at all.
    wanted = next((name for name in tools if re.split(r"__|-", name)[-1] == TOOL), None)
    if wanted is not None:
        return "tool", wanted
    return "text", json.dumps({"status": "done", "summary": "Triaged ACME-1 without reading it."})


def _text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            _text_of(part.get("text") or part.get("content") or "")
            for part in value
            if isinstance(part, dict)
        )
    return ""


def _sse(events: list[tuple[str | None, dict[str, Any] | str]]) -> bytes:
    lines = []
    for name, data in events:
        payload = data if isinstance(data, str) else json.dumps(data)
        lines.append((f"event: {name}\n" if name else "") + f"data: {payload}\n\n")
    return "".join(lines).encode()


def _chat(body: dict[str, Any]) -> tuple[str, bytes]:
    tools = [t.get("function", {}).get("name", "") for t in body.get("tools") or []]
    answered = next(
        (_text_of(m.get("content")) for m in reversed(body.get("messages") or []) if m.get("role") == "tool"),
        None,
    )
    kind, value = _decide(tools, answered)
    created, model, ident = int(time.time()), str(body.get("model") or ""), f"chatcmpl-{uuid.uuid4().hex}"
    usage = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
    call = {
        "id": f"call_{uuid.uuid4().hex[:12]}",
        "type": "function",
        "function": {"name": value, "arguments": json.dumps({"key": "ACME-1"})},
    }
    message: dict[str, Any] = (
        {"role": "assistant", "content": None, "tool_calls": [call]}
        if kind == "tool"
        else {"role": "assistant", "content": value}
    )
    finish = "tool_calls" if kind == "tool" else "stop"
    if not body.get("stream"):
        return "application/json", json.dumps(
            {
                "id": ident,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": usage,
            }
        ).encode()
    head = {"id": ident, "object": "chat.completion.chunk", "created": created, "model": model}
    delta: dict[str, Any] = {"tool_calls": [{"index": 0, **call}]} if kind == "tool" else {"content": value}
    chunks: list[tuple[str | None, dict[str, Any] | str]] = [
        (
            None,
            {
                **head,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}
                ],
            },
        ),
        (None, {**head, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}),
        (None, {**head, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}),
    ]
    if (body.get("stream_options") or {}).get("include_usage"):
        chunks.append((None, {**head, "choices": [], "usage": usage}))
    chunks.append((None, "[DONE]"))
    return "text/event-stream", _sse(chunks)


def _responses(body: dict[str, Any]) -> tuple[str, bytes]:
    tools = [str(t.get("name") or "") for t in body.get("tools") or [] if t.get("type") == "function"]
    items = body.get("input") if isinstance(body.get("input"), list) else []
    answered = next(
        (
            str(i.get("output") or "")
            for i in reversed(items)
            if isinstance(i, dict) and i.get("type") == "function_call_output"
        ),
        None,
    )
    kind, value = _decide(tools, answered)
    item: dict[str, Any] = (
        {
            "type": "function_call",
            "id": f"fc_{uuid.uuid4().hex[:12]}",
            "call_id": f"call_{uuid.uuid4().hex[:12]}",
            "name": value,
            "arguments": json.dumps({"key": "ACME-1"}),
            "status": "completed",
        }
        if kind == "tool"
        else {
            "type": "message",
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": value, "annotations": []}],
        }
    )
    response = {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": str(body.get("model") or ""),
        "output": [item],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }
    if not body.get("stream"):
        return "application/json", json.dumps(response).encode()
    # As OpenAI streams it: a function call is added with no arguments, which come in their own
    # events; a client that reads them only from there (Agent Framework's) sees no call otherwise.
    added = {**item, "arguments": ""} if kind == "tool" else item
    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "output_index": 0, "item": added},
    ]
    if kind == "tool":
        events += [
            {
                "type": f"response.function_call_arguments.{step}",
                "output_index": 0,
                "item_id": item["id"],
                **({"delta": item["arguments"]} if step == "delta" else {"arguments": item["arguments"]}),
            }
            for step in ("delta", "done")
        ]
    if kind == "text":
        events.append(
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "item_id": item["id"],
                "delta": value,
            }
        )
    events += [
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response},
    ]
    return "text/event-stream", _sse([(e["type"], {**e, "sequence_number": n}) for n, e in enumerate(events)])


def _messages(body: dict[str, Any]) -> tuple[str, bytes]:
    tools = [str(t.get("name") or "") for t in body.get("tools") or []]
    answered = None
    for message in reversed(body.get("messages") or []):
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, list):
            results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            if results:
                answered = _text_of(results[-1].get("content"))
            break
    kind, value = _decide(tools, answered)
    block: dict[str, Any] = (
        {
            "type": "tool_use",
            "id": f"toolu_{uuid.uuid4().hex[:20]}",
            "name": value,
            "input": {"key": "ACME-1"},
        }
        if kind == "tool"
        else {"type": "text", "text": value}
    )
    stop = "tool_use" if kind == "tool" else "end_turn"
    message = {
        "id": f"msg_{uuid.uuid4().hex[:20]}",
        "type": "message",
        "role": "assistant",
        "model": str(body.get("model") or ""),
        "content": [block],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 20, "output_tokens": 10},
    }
    if not body.get("stream"):
        return "application/json", json.dumps(message).encode()
    start = {**block, "input": {}} if kind == "tool" else {"type": "text", "text": ""}
    delta = (
        {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
        if kind == "tool"
        else {"type": "text_delta", "text": value}
    )
    events: list[tuple[str | None, dict[str, Any] | str]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    **message,
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 20, "output_tokens": 1},
                },
            },
        ),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": start}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 10},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "text/event-stream", _sse(events)


@contextmanager
def fake_gateway() -> Iterator[FakeGateway]:
    gateway = FakeGateway()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            self._send(
                200,
                "application/json",
                json.dumps(
                    {"object": "list", "data": [{"id": "platform/standard", "object": "model"}]}
                ).encode(),
            )

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
            path = self.path.split("?")[0].rstrip("/")
            key = self.headers.get("x-api-key") or self.headers.get("authorization", "").removeprefix(
                "Bearer "
            )
            gateway.calls.append(
                GatewayCall(path, key, str(body.get("model") or ""), bool(body.get("stream")))
            )
            if key != RUN_KEY:
                self._send(
                    401, "application/json", b'{"error": {"message": "invalid key", "type": "auth_error"}}'
                )
            elif path.endswith("/chat/completions"):
                self._send(200, *_chat(body))
            elif path.endswith("/responses"):
                self._send(200, *_responses(body))
            elif path.endswith("/messages/count_tokens"):
                self._send(200, "application/json", b'{"input_tokens": 20}')
            elif path.endswith("/messages"):
                self._send(200, *_messages(body))
            else:
                self._send(
                    404, "application/json", json.dumps({"error": {"message": f"no route {path}"}}).encode()
                )

        def _send(self, status: int, content_type: str, content: bytes) -> None:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    with serve(Handler) as url:
        gateway.url = url
        yield gateway


# --- the MCP server ---------------------------------------------------------------------------------


@dataclass
class FakeTickets:
    url: str = ""
    #: (method, the credential header it came with) of every JSON-RPC message.
    seen: list[tuple[str, str]] = field(default_factory=list)


@contextmanager
def fake_tickets() -> Iterator[FakeTickets]:
    """A streamable-HTTP MCP server with one tool, `get_ticket`, answered in JSON."""
    tickets = FakeTickets()
    session = uuid.uuid4().hex

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def do_DELETE(self) -> None:
            self._send(200, b"")

        def do_GET(self) -> None:
            self._send(405, b"")

        def do_POST(self) -> None:
            message = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
            method = str(message.get("method") or "")
            tickets.seen.append((method, self.headers.get("authorization", "")))
            if "id" not in message:
                self._send(202, b"")
                return
            if method == "initialize":
                version = (message.get("params") or {}).get("protocolVersion") or "2025-06-18"
                result: dict[str, Any] = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "tickets", "version": "1.0.0"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": TOOL,
                            "description": "Reads a ticket by its key.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"key": {"type": "string"}},
                                "required": ["key"],
                            },
                        }
                    ]
                }
            elif method == "tools/call":
                key = ((message.get("params") or {}).get("arguments") or {}).get("key", "")
                text = f"{key}: the payment fails after checkout; {TICKET_FACT}."
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            elif method == "ping":
                result = {}
            else:
                self._send(
                    200,
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {"code": -32601, "message": f"no method {method}"},
                        }
                    ).encode(),
                )
                return
            self._send(200, json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode())

        def _send(self, status: int, content: bytes) -> None:
            self.send_response(status)
            if content:
                self.send_header("content-type", "application/json")
            self.send_header("mcp-session-id", session)
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    with serve(Handler) as url:
        tickets.url = f"{url}/mcp"
        yield tickets


# --- the agent under test ---------------------------------------------------------------------------


@dataclass
class HostedAgentSpec:
    """A process (`command`, run in `cwd`) or an image (run through `docker run`)."""

    command: list[str] = field(default_factory=list)
    cwd: str | None = None
    image: str | None = None
    protocol: str = "responses"
    readiness_timeout_s: int = 60
    termination_grace_s: int = 10
    #: Variables of the agent's own, the way a version declares them (no credential).
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: dict[str, Any] | list[str]) -> HostedAgentSpec:
        return cls(command=list(raw)) if isinstance(raw, list) else cls(**raw)


def container_command(image: str, env: dict[str, str], name: str, *, network: str = "host") -> list[str]:
    """`docker run` for an agent in an image: the host's network (the runner's listeners are on the
    host's loopback, as they are on the pod's), the environment passed by name, no capability.

    `network` is another network namespace (`container:<relay>`) where Docker's "host" is not the
    machine that runs the harness (see `needs_relay`)."""
    flags: list[str] = []
    for variable in sorted(env):
        flags += ["-e", variable]
    return [
        "docker", "run", "--rm", "--name", name, f"--network={network}", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", *flags, image,
    ]  # fmt: skip


#: Forwards a loopback port of the agent's namespace to the same port on the harness's host.
RELAY_IMAGE = "alpine/socat:1.8.0.3"
NETWORK_ENV = "CHOREGOS_CONFORMANCE_DOCKER_NETWORK"


def needs_relay() -> bool:
    """Whether `--network=host` is not the harness's own loopback.

    Under Docker Desktop the daemon runs in a VM: its "host" network is the VM's, so an agent
    started on it never reaches the listeners of the harness (S41-23). `CHOREGOS_CONFORMANCE_DOCKER_NETWORK`
    (`host` or `relay`) forces the answer; otherwise Docker Desktop is told from `docker info`."""
    forced = os.environ.get(NETWORK_ENV, "")
    if forced:
        return forced == "relay"
    info = subprocess.run(
        ["docker", "info", "--format", "{{.OperatingSystem}}"], capture_output=True, text=True, check=False
    )
    return "Docker Desktop" in info.stdout


def relay_command(name: str, listeners: list[int], published: int) -> list[str]:
    """A container whose network namespace the agent joins, as a pod's containers share one: each
    of the harness's `listeners` is reachable at the loopback of the namespace (so the agent's
    variables, which name `127.0.0.1`, are unchanged), and the agent's own port is published back
    to the harness's loopback."""
    forwards = " & ".join(
        f"socat TCP-LISTEN:{port},bind=127.0.0.1,fork,reuseaddr "
        f"TCP:host.docker.internal:{port},retry=8,interval=0.25"
        for port in listeners
    )
    return [
        "docker", "run", "-d", "--name", name, "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--add-host=host.docker.internal:host-gateway", "-p", f"127.0.0.1:{published}:{published}",
        "--entrypoint", "sh", RELAY_IMAGE, "-c", f"{forwards} & wait",
    ]  # fmt: skip


#: What a process needs of the host to start at all; nothing else of the host's environment is passed.
_HOST_ENV = ("PATH", "HOME", "LANG", "TMPDIR", "SYSTEMROOT")


class AgentProcess:
    def __init__(self, spec: HostedAgentSpec, env: dict[str, str]) -> None:
        self.spec, self.env = spec, env
        self.name = f"choregos-conformance-{uuid.uuid4().hex[:8]}"
        self.process: subprocess.Popen[bytes] | None = None
        #: The container that holds the agent's network namespace, where the host's is not the harness's.
        self.relay: str | None = None
        self.log = Path(os.environ.get("TMPDIR", "/tmp")) / f"{self.name}.log"

    def start(self) -> None:
        host = {k: os.environ[k] for k in _HOST_ENV if k in os.environ}
        env = {**host, **self.env}
        if self.spec.image and needs_relay():
            self.relay = f"{self.name}-net"
            listeners = [
                int(self.env[k].rsplit(":", 1)[1].split("/")[0])
                for k in ("OPENAI_BASE_URL", "CHOREGOS_MCP_URL", "HTTPS_PROXY")
            ]
            for _ in range(2):  # the published port can be taken between its choice and the start
                subprocess.run(["docker", "rm", "-f", self.relay], capture_output=True, check=False)
                started = subprocess.run(
                    relay_command(self.relay, listeners, int(self.env["PORT"])),
                    capture_output=True,
                    check=False,
                )
                if started.returncode == 0:
                    break
            if started.returncode != 0:
                raise RuntimeError(f"the network relay did not start: {started.stderr.decode()[-500:]}")
        command = (
            container_command(
                self.spec.image,
                self.env,
                self.name,
                network=f"container:{self.relay}" if self.relay else "host",
            )
            if self.spec.image
            else self.spec.command
        )
        with self.log.open("wb") as log:
            self.process = subprocess.Popen(
                command, cwd=self.spec.cwd, env=env, stdout=log, stderr=subprocess.STDOUT
            )

    def terminate(self, grace: float) -> float | None:
        """Sends SIGTERM and says how long the agent took to exit, `None` past `grace`."""
        assert self.process is not None
        started = time.monotonic()
        if self.spec.image:
            subprocess.run(
                ["docker", "kill", "--signal", "TERM", self.name], capture_output=True, check=False
            )
        else:
            self.process.send_signal(signal.SIGTERM)
        try:
            self.process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            return None
        return time.monotonic() - started

    def kill(self) -> None:
        if self.process is not None and self.process.poll() is None:
            if self.spec.image:
                subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, check=False)
            self.process.kill()
            self.process.wait(timeout=10)
        if self.relay:
            subprocess.run(["docker", "rm", "-f", self.relay], capture_output=True, check=False)

    def tail(self, lines: int = 15) -> str:
        try:
            return "\n".join(self.log.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""


def hosted_stage_input(
    spec: HostedAgentSpec, gateway: FakeGateway, tickets: FakeTickets, *, max_turns: int, port: int
) -> StageInput:
    return StageInput(
        run_id=f"conformance-hosted-{uuid.uuid4().hex[:6]}",
        attempt=1,
        project=ProjectRef(slug="conformance", org="varga"),
        work_item=WorkItemRef(key="ACME-1", title="Checkout fails with an error after payment"),
        transition=TransitionRef(id="t-triage", role="triage", **{"from": "inbox"}, to="triaged"),
        agent=AgentRef(backend="hosted"),
        model=ModelRef(litellm_model="platform/standard", base_url=f"{gateway.url}/v1", api_format="openai"),
        gateway_key=RUN_KEY,
        budget=Budget(usd=1, max_turns=max_turns, max_minutes=5),
        allowed_paths=["**"],
        playbook=PlaybookRef(ref="triage@conformance", prompt="Triage the ticket."),
        permissions=Permissions(egress="listed"),
        tools=ToolsRef(mcp={"tickets": McpServerRef(url=tickets.url, env={"Authorization": TICKETS_TOKEN})}),
        hosted=HostedCall(
            protocol=spec.protocol,  # type: ignore[arg-type]
            image=IMAGE,
            port=port,
            readiness_timeout_s=spec.readiness_timeout_s,
            termination_grace_s=spec.termination_grace_s,
            env=spec.env,
            agent_slug="conformance",
            agent_version=1,
        ),
        callbacks=Callbacks(api_url="http://localhost:0/internal", run_token="tok"),
    )


@dataclass
class Run:
    status: str
    text: str
    error: str | None
    turns: int
    tunnels: int
    refused_tunnels: int


async def _one_run(stage_input: StageInput, agent: AgentProcess, timeout_s: float) -> Run:
    events: list[tuple[str, dict[str, Any]]] = []

    async def record(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    guards = GuardRails(stage_input.permissions, list(stage_input.allowed_paths), workspace=str(ROOT))
    ports = tuple(
        int(agent.env[k].rsplit(":", 1)[1].split("/")[0])
        for k in ("OPENAI_BASE_URL", "CHOREGOS_MCP_URL", "HTTPS_PROXY")
    )
    model = ModelProxy(stage_input, record)
    mcp = McpProxy(stage_input, record, guards)
    connect = ConnectProxy(stage_input, record)
    async with Loopback(model, mcp, connect, ports=ports):  # type: ignore[arg-type]
        try:
            step = await asyncio.wait_for(
                run_hosted(
                    stage_input,
                    "Triage the ticket ACME-1: read it with your tools, then answer.",
                    record,
                    interval=0.2,
                ),
                timeout=timeout_s,
            )
            status, text, error = step.status, step.text, step.error
        except TimeoutError:
            status, text, error = "timeout", "", f"no answer within {timeout_s} s"
    return Run(status, text, error, model.turns, connect.tunnels, connect.refused)


async def run_hosted_suite(name: str, spec: HostedAgentSpec, *, timeout_s: float = 120) -> ConformanceResult:
    """The eight checks against one hosted agent (see the module's table)."""
    result = ConformanceResult(backend=f"hosted:{name}")

    def verdict(check: str, ok: bool, why: str) -> None:
        if ok:
            result.passed.append(check)
        else:
            result.failed[check] = why

    with fake_gateway() as gateway, fake_tickets() as tickets, outside_model() as outside:
        pick = published_port if spec.image and needs_relay() else free_port
        loopback = (pick(), pick(), pick())
        port = pick()
        for limit, label in ((8, "first"), (1, "bounded")):
            stage_input = hosted_stage_input(spec, gateway, tickets, max_turns=limit, port=port)
            assert stage_input.hosted is not None
            env = sidecar_env(
                stage_input.hosted,
                run_id=stage_input.run_id,
                model=stage_input.model.litellm_model,
                mcp_servers=["tickets"],
                ports=loopback,
            )
            env["CONFORMANCE_OUTSIDE_MODEL"] = outside.url  # read by the rogue agent only
            agent = AgentProcess(spec, env)
            asked_before, mcp_before = len(gateway.calls), len(tickets.seen)
            agent.start()
            try:
                run = await _one_run(stage_input, agent, timeout_s)
                calls = gateway.calls[asked_before:]
                if label == "first":
                    tail = agent.tail()
                    verdict("readiness", run.status != "not_ready", f"{run.error}: {tail}")
                    verdict("answer", run.status == "answered", f"{run.status}: {run.error}: {tail}")
                    foreign = [c.key for c in calls if c.key != RUN_KEY]
                    verdict(
                        "model_at_loopback",
                        bool(calls) and not foreign and not outside.calls,
                        f"{len(calls)} calls at the gateway, foreign keys {foreign}, "
                        f"{len(outside.calls)} outside the loopback",
                    )
                    tool_calls = [key for method, key in tickets.seen[mcp_before:] if method == "tools/call"]
                    verdict(
                        "mcp_at_loopback",
                        bool(tool_calls) and all(key == f"Bearer {TICKETS_TOKEN}" for key in tool_calls),
                        f"tools/call seen {len(tool_calls)} times at the MCP server "
                        f"(methods seen: {[method for method, _ in tickets.seen[mcp_before:]]})",
                    )
                    with tempfile.TemporaryDirectory() as workspace:
                        step_result, why = (
                            result_of(stage_input, Path(workspace), run.text)
                            if run.status == "answered"
                            else (None, run.error)
                        )
                    verdict(
                        "result_valid",
                        step_result is not None and TICKET_FACT in (step_result.summary or ""),
                        why or f"the result does not quote the ticket: {run.text[:200]!r}",
                    )
                    verdict(
                        "no_egress",
                        run.tunnels == 0 and run.refused_tunnels == 0,
                        f"{run.tunnels} tunnels asked, {run.refused_tunnels} refused",
                    )
                    took = agent.terminate(spec.termination_grace_s)
                    verdict(
                        "sigterm",
                        took is not None,
                        f"still running {spec.termination_grace_s} s after SIGTERM",
                    )
                else:
                    verdict(
                        "max_turns",
                        len(calls) <= limit and run.status not in ("timeout", "not_ready"),
                        f"{len(calls)} calls at the gateway for {limit} allowed, the step ended {run.status}",
                    )
            finally:
                agent.kill()
    return result


@dataclass
class OutsideModel:
    url: str = ""
    calls: list[str] = field(default_factory=list)


@contextmanager
def outside_model() -> Iterator[OutsideModel]:
    """A model the agent could reach without the loopback: the rogue `direct_model` agent does."""
    outside = OutsideModel()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
            outside.calls.append(self.path)
            _, content = _chat({**body, "stream": False})
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    with serve(Handler) as url:
        outside.url = f"{url}/v1"
        yield outside


def python_example(directory: str, python: str | None = None) -> HostedAgentSpec:
    """An example of `examples/hosted/` run as a process: `responses_server.py` in its directory, or
    `main.py` for one that brings its own server (Foundry's hosting adapter, S41-19)."""
    path = ROOT / "examples" / "hosted" / directory
    server = path / "responses_server.py"
    entry = server if server.exists() else path / "main.py"
    return HostedAgentSpec(command=[python or sys.executable, str(entry)], cwd=str(path))


def node_example(directory: str, node: str | None = None) -> HostedAgentSpec:
    """A Node example of `examples/hosted/` run as a process: `responses_server.mjs` in its directory.

    Without `node` on the machine, the command is the bare name, and the test skips it."""
    path = ROOT / "examples" / "hosted" / directory
    return HostedAgentSpec(
        command=[node or shutil.which("node") or "node", str(path / "responses_server.mjs")], cwd=str(path)
    )
