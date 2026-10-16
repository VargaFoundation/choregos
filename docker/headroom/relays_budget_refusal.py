# SPDX-License-Identifier: Apache-2.0
"""Does headroom hand the gateway's budget refusal to the agent as the gateway wrote it? (S37-12)

The run's virtual key is the hard cap (ADR 0047): once it is spent, LiteLLM answers every model
call with a 400 `budget_exceeded`, and the runner reads that refusal in what the agent relays
(`choregos_runner.budget`). With headroom between the agent and the gateway (ADR 0055), the agent
no longer sees the gateway's answer, it sees headroom's. A proxy that wrapped the error, turned it
into a 502 or retried it would make a spent budget look like a silent agent, which the workflow
retries with a fresh key at the full stage budget.

Run at image build, with no network: a fake gateway that refuses for budget, the image's own
headroom in front of it, and the runner's own detector reading what comes back, for the Anthropic
and OpenAI wires, streamed or not. Any case that is not a 4xx carrying a detected refusal fails the
build.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from choregos_runner.budget import gateway_budget_refusal

GATEWAY_PORT = 9400
PROXY_PORT = 8787
#: LiteLLM's answer for a spent key, as its proxy writes it.
REFUSAL = {
    "error": {
        "message": "Budget has been exceeded! Current cost: 0.51, Max budget: 0.5",
        "type": "budget_exceeded",
        "param": None,
        "code": "400",
    }
}


class _Gateway(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("content-length", 0)))
        body = json.dumps(REFUSAL).encode()
        self.send_response(400)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def _call(path: str, payload: dict[str, object], headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{PROXY_PORT}{path}",
        json.dumps(payload).encode(),
        {"content-type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def main() -> int:
    gateway = HTTPServer(("127.0.0.1", GATEWAY_PORT), _Gateway)
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{GATEWAY_PORT}"
    proxy = subprocess.Popen(
        [
            "headroom",
            "proxy",
            "--host",
            "127.0.0.1",
            "--port",
            str(PROXY_PORT),
            "--stateless",
            "--mode",
            "cache",
            "--anthropic-api-url",
            upstream,
            "--openai-api-url",
            f"{upstream}/v1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for _ in range(90):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PROXY_PORT}/health", timeout=1).close()
                break
            except OSError:
                time.sleep(1)
        else:
            print("headroom never answered /health", file=sys.stderr)
            return 1
        messages = [{"role": "user", "content": "hello"}]
        anthropic = {"x-api-key": "sk-run", "anthropic-version": "2023-06-01"}
        openai = {"authorization": "Bearer sk-run"}
        cases = {
            "anthropic": ("/v1/messages", {"model": "m", "max_tokens": 8, "messages": messages}, anthropic),
            "anthropic, streamed": (
                "/v1/messages",
                {"model": "m", "max_tokens": 8, "stream": True, "messages": messages},
                anthropic,
            ),
            "openai": ("/v1/chat/completions", {"model": "m", "messages": messages}, openai),
            "openai, streamed": (
                "/v1/chat/completions",
                {"model": "m", "stream": True, "messages": messages},
                openai,
            ),
        }
        failed = []
        for name, (path, payload, headers) in cases.items():
            status, body = _call(path, payload, headers)
            seen = gateway_budget_refusal(body.splitlines())
            print(f"{name}: {status}, refusal {'seen' if seen else 'LOST'}")
            if not (400 <= status < 500 and seen):
                failed.append(f"{name}: {status} {body[:300]!r}")
        for line in failed:
            print(f"headroom did not relay the budget refusal — {line}", file=sys.stderr)
        return 1 if failed else 0
    finally:
        proxy.terminate()
        try:
            proxy.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proxy.kill()
        gateway.shutdown()


if __name__ == "__main__":
    sys.exit(main())
