#!/usr/bin/env python3
"""A scriptable headroom (ADR 0005): `fake_headroom.py SCRIPT proxy --host … --port … …`.

It takes the arguments of `headroom proxy`, writes down what it was started with (arguments,
environment, working directory) and, unless the script says otherwise, answers `/livez` and
forwards every POST to its `--anthropic-api-url`, marking it `x-fake-headroom: 1` so the gateway
can tell what came through it. Like headroom, it leaves a file in its working directory.

The script (JSON): `record` (where to write down the start), `exit` (exit at once with that code),
`silent` (run, never answer), `removed` (the tokens it says it removed from each request it
forwarded), `retrievals` (the originals it says the model asked back), `stats` (`down`: `/stats`
does not answer).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

FORWARDED = ("content-type", "x-api-key", "authorization")
VERSION = "0.0.0-fake"


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as source:
        script: dict[str, Any] = json.load(source)
    argv = sys.argv[2:]
    if record := script.get("record"):
        with open(record, "w", encoding="utf-8") as out:
            json.dump({"argv": argv, "env": dict(os.environ), "cwd": os.getcwd(), "pid": os.getpid()}, out)
    with open("proxy-state.json", "w", encoding="utf-8") as state:
        state.write("{}")
    if "exit" in script:
        sys.stderr.write("fake headroom: told to exit\n")
        return int(script["exit"])
    if script.get("silent"):
        time.sleep(3600)
        return 0
    # Like headroom: the upstream loses its `/v1`, the request's path keeps its own.
    upstream = _flag(argv, "--anthropic-api-url").rstrip("/").removesuffix("/v1")
    forwarded_requests: list[int] = []
    removed = int(script.get("removed", 0))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/livez":
                self._answer(200, {"alive": True, "version": VERSION})
            elif self.path == "/stats" and script.get("stats") != "down":
                # The shape of headroom 0.39.1's `/stats`, the counters the runner reads.
                count = len(forwarded_requests)
                self._answer(
                    200,
                    {
                        "summary": {
                            "api_requests": count,
                            "compression": {
                                "requests_compressed": count if removed else 0,
                                "total_tokens_before": sum(forwarded_requests),
                                "total_tokens_removed": removed * count,
                            },
                            # Counters the runner must not read: headroom's MCP server, its
                            # dollar figures from a price list that is not ours.
                            "mcp": {"retrievals": 99},
                            "cost": {"total_saved_usd": 12.5},
                        },
                        "feedback_loop": {"total_retrievals": int(script.get("retrievals", 0))},
                    },
                )
            else:
                self._answer(404, {})

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            forwarded_requests.append(len(body))
            headers = {k: v for k, v in self.headers.items() if k.lower() in FORWARDED}
            headers["x-fake-headroom"] = "1"
            forwarded = urllib.request.Request(upstream + self.path, body, headers, method="POST")
            with urllib.request.urlopen(forwarded, timeout=10) as answer:
                self._answer(answer.status, json.loads(answer.read() or b"{}"))

        def _answer(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: Any) -> None:
            return None

    ThreadingHTTPServer((_flag(argv, "--host"), int(_flag(argv, "--port"))), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
