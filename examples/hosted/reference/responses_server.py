# SPDX-License-Identifier: Apache-2.0
"""Foundry's hosted-agent contract on `$PORT`, with the standard library only (ADR 0080).

Every example of `examples/hosted/` carries this file unchanged (a test refuses a copy that differs):
no shared library, as for code agents (ADR 0063). It serves `GET /readiness`, `POST /responses` (the
Responses API, as server-sent events when the body asks `stream: true`) and `POST /invocations` (a
JSON body in, `{"output": ...}` out), and hands the text of the request to `agent.answer(text)`, the
one function an example writes, plain or `async`. On SIGTERM it stops accepting, lets the request in
flight finish and exits 0.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import agent


def request_text(body: dict[str, Any]) -> str:
    """The text of a request: `input` as a string, or the text parts of its messages."""
    value = body.get("input", "")
    if isinstance(value, str):
        return value
    parts: list[str] = []
    for item in value if isinstance(value, list) else []:
        content = item.get("content") if isinstance(item, dict) else item
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts += [str(c.get("text") or "") for c in content if isinstance(c, dict)]
    return "\n".join(part for part in parts if part)


def run_agent(text: str) -> str:
    answer = agent.answer(text)
    if inspect.iscoroutine(answer):
        answer = asyncio.run(answer)
    return answer if isinstance(answer, str) else json.dumps(answer)


def response_object(text: str, *, status: str = "completed", error: str = "") -> dict[str, Any]:
    message = {
        "type": "message",
        "id": f"msg_{uuid.uuid4().hex}",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": os.environ.get("CHOREGOS_MODEL", ""),
        "output": [message] if text else [],
        "error": {"code": "server_error", "message": error} if error else None,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/readiness":
            self._send(200, "application/json", b'{"status":"ready"}')
        else:
            self._send(404, "application/json", b'{"error":"not found"}')

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, "application/json", b'{"error":"the body is not JSON"}')
            return
        if self.path == "/invocations":
            self._invocations(body)
        elif self.path == "/responses":
            self._responses(body)
        else:
            self._send(404, "application/json", b'{"error":"not found"}')

    def _invocations(self, body: dict[str, Any]) -> None:
        try:
            output = run_agent(request_text(body))
        except Exception as exc:
            traceback.print_exc()
            self._send(500, "application/json", json.dumps({"error": str(exc)}).encode())
            return
        self._send(200, "application/json", json.dumps({"output": output}).encode())

    def _responses(self, body: dict[str, Any]) -> None:
        try:
            response = response_object(run_agent(request_text(body)))
        except Exception as exc:
            traceback.print_exc()
            response = response_object("", status="failed", error=str(exc)[:500])
        if not body.get("stream"):
            self._send(200, "application/json", json.dumps(response).encode())
            return
        text = "".join(part["text"] for item in response["output"] for part in item["content"])
        final = "response.completed" if response["status"] == "completed" else "response.failed"
        events = [
            {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            *([{"type": "response.output_text.delta", "output_index": 0, "delta": text}] if text else []),
            {"type": final, "response": response},
        ]
        stream = "".join(
            f"event: {e['type']}\ndata: {json.dumps({**e, 'sequence_number': n})}\n\n"
            for n, e in enumerate(events)
        )
        self._send(200, "text/event-stream", stream.encode())

    def _send(self, status: int, content_type: str, content: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    # Every interface of the container: the runner reaches it on the pod's loopback.
    address = (os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8088")))  # noqa: S104
    server = ThreadingHTTPServer(address, Handler)
    # The request in flight finishes before the process exits.
    server.daemon_threads = False
    server.block_on_close = True
    stop = threading.Event()

    def on_sigterm(*_: object) -> None:
        if not stop.is_set():
            stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, on_sigterm)
    signal.signal(signal.SIGINT, on_sigterm)
    print(f"listening on {server.server_address[0]}:{server.server_address[1]}", flush=True)
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
