"""A stand-in for the platform's API and the model gateway, for `test_hosted_session_pod.py` (S41-24).

It runs in a pod of the test's namespace and plays, over plain HTTP:

- `/api/v1/internal/agent-sessions/s1/...`: the routes a session pod's runner uses (S41-11);
- `/v1/chat/completions`: a model that calls the session tool once, then answers with its output.

Every call is logged as one JSON line, with whether the bearer it carried was the one expected.
Standard library only: it runs in the reference example's image.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PREFIX = "/api/v1/internal/agent-sessions/s1"
TOKEN = os.environ["EXPECTED_TOKEN"]
KEY = os.environ["EXPECTED_KEY"]
with open("/stub/stage_input.json", encoding="utf-8") as handle:
    STAGE_INPUT = handle.read()

LOCK = threading.Lock()
STATE: dict[str, Any] = {"turn": "new", "model_calls": 0}


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}), flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: int, body: Any = None, *, raw: bytes | None = None) -> None:
        payload = raw if raw is not None else b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("content-length") or 0)
        return dict(json.loads(self.rfile.read(size) or b"{}")) if size else {}

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        authorized = self.headers.get("authorization") == f"Bearer {TOKEN}"
        if path == "/healthz":
            self._send(200, {"ok": True})
        elif path == f"{PREFIX}/input":
            log("input", authorized=authorized)
            self._send(200, raw=STAGE_INPUT.encode())
        elif path == f"{PREFIX}/turns/next":
            with LOCK:
                state = STATE["turn"]
                if state == "new":
                    STATE["turn"] = "handed"
            if state == "new":
                log("turn.handed", authorized=authorized)
                turn = {"turn_id": "resp_s41_24", "input": "triage ticket 42", "protocol": "responses"}
                self._send(200, turn)
            elif state == "done":
                log("session.closed")
                self._send(410, {"detail": "session s1 is closed (closed)"})
            else:
                time.sleep(1)
                self._send(204)
        elif path == f"{PREFIX}/tools":
            log("tools.list", authorized=authorized)
            self._send(200, {"tools": [{"name": "search_tickets", "inputSchema": {"type": "object"}}]})
        else:
            self._send(404, {"detail": path})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        body = self._body()
        authorized = self.headers.get("authorization") == f"Bearer {TOKEN}"
        if path == f"{PREFIX}/tools/search_tickets":
            log("tools.call", authorized=authorized, arguments=body.get("arguments"))
            self._send(200, {"text": "3 tickets found."})
        elif path.startswith(f"{PREFIX}/turns/"):
            log("turn.answered", authorized=authorized, turn=path.rsplit("/", 1)[-1], answer=body)
            with LOCK:
                STATE["turn"] = "done"
            self._send(200, {"status": "recorded"})
        elif path == "/v1/chat/completions":
            keyed = self.headers.get("authorization") == f"Bearer {KEY}"
            with LOCK:
                STATE["model_calls"] += 1
                call = STATE["model_calls"]
            log("model.call", keyed=keyed, call=call, model=body.get("model"))
            if call == 1:
                message: dict[str, Any] = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "search_tickets", "arguments": json.dumps({"query": "42"})},
                        }
                    ],
                }
            else:
                tool = [m for m in body.get("messages", []) if m.get("role") == "tool"]
                message = {"role": "assistant", "content": "Ticket 42 triaged: " + str(tool[-1]["content"])}
            self._send(200, {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})
        else:
            self._send(404, {"detail": path})


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
