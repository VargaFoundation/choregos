"""The MCP server behind an outpost's alias, for `test_outpost_tools_on_cluster.py` (S65-19).

It runs in a pod of the outpost's namespace and answers MCP over Streamable HTTP (plain JSON
answers): `initialize`, its notification, `tools/list` and `tools/call`. It opens only with the
credential the outpost's policy names (`Authorization: Bearer <EXPECTED_CREDENTIAL>`), and logs
each request as one JSON line: the method, the tool, and whether the credential was the one expected.
Standard library only: it runs in any image that has a Python.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

CREDENTIAL = os.environ["EXPECTED_CREDENTIAL"]
SESSION = "outpost-test-session"
TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_issues",
        "description": "the issues that match",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {"name": "create_issue", "description": "opens an issue", "inputSchema": {"type": "object"}},
]


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}), flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self._send(200 if self.path == "/healthz" else 405, {"status": "ok"})

    def do_DELETE(self) -> None:
        self._send(200)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        message = json.loads(self.rfile.read(length) or b"{}")
        method = str(message.get("method", ""))
        params = message.get("params") or {}
        authorized = self.headers.get("authorization") == f"Bearer {CREDENTIAL}"
        log("request", method=method, tool=params.get("name"), authorized=authorized)
        if not authorized:
            self._send(401, {"error": "unauthorized"})
            return
        if "id" not in message:
            self._send(202)
            return
        if method == "initialize":
            result: Any = {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "outpost-test-mcp", "version": "1"},
            }
            answer = {"jsonrpc": "2.0", "id": message["id"], "result": result}
            self._send(200, answer, {"Mcp-Session-Id": SESSION})
            return
        if self.headers.get("mcp-session-id") != SESSION:
            self._send(400, {"error": "missing session"})
            return
        if method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = str(params.get("name"))
            text = f"{name} answered by the server behind the alias: {json.dumps(params.get('arguments'))}"
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        else:
            error = {"code": -32601, "message": f"unknown method: {method}"}
            self._send(200, {"jsonrpc": "2.0", "id": message["id"], "error": error})
            return
        self._send(200, {"jsonrpc": "2.0", "id": message["id"], "result": result})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
