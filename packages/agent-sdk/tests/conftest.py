"""Fixtures for the SDK's protocol tests: a gateway, an MCP server and a runner-side client."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer

AGENTS = Path(__file__).parent / "agents"
RUN_KEY = "run-" + "k" * 12  # built, never a literal that looks like a key


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class Gateway:
    url: str
    seen: list[dict[str, str]] = field(default_factory=list)


@pytest.fixture
def gateway() -> Iterator[Gateway]:
    """An OpenAI-compatible endpoint that records the Authorization header of each call."""
    seen: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            seen.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("authorization", ""),
                    "model": body.get("model", ""),
                }
            )
            reply = {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model", "m"),
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            payload = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Gateway(url=f"http://127.0.0.1:{server.server_address[1]}/v1", seen=seen)
    server.shutdown()


@dataclass
class McpFake:
    url: str
    #: Every event of the test, in order: the guard's decisions and the tool's own calls.
    events: list[str]


@pytest.fixture
async def mcp_server() -> AsyncIterator[McpFake]:
    """A real MCP server (streamable HTTP) with one tool, `knowledge_search`."""
    events: list[str] = []
    server = MCPServer("fake-tools")

    @server.tool()
    def knowledge_search(query: str) -> str:
        events.append(f"mcp:knowledge_search:{query}")
        return f"hit for {query}"

    port = free_port()
    uv = uvicorn.Server(
        uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(uv.serve())
    for _ in range(100):
        if uv.started:
            break
        await asyncio.sleep(0.05)
    yield McpFake(url=f"http://127.0.0.1:{port}/mcp", events=events)
    uv.should_exit = True
    await task


def agent_command(name: str) -> list[str]:
    return [sys.executable, str(AGENTS / f"{name}.py")]
