"""A hosted agent that breaks the contract one way, chosen by `ROGUE`: the suite must see each.

It is the reference example (`examples/hosted/reference`) with one thing changed:

- `never_ready`: `GET /readiness` always answers 503;
- `direct_model`: the model is called at an address outside the loopback, the way an agent with
  its own provider key and URL would;
- `no_tools`: the agent never looks at the MCP servers;
- `stray_egress`: before answering, it opens a tunnel to a telemetry host, the way an SDK's tracing
  does when left on;
- `ignores_sigterm`: SIGTERM is ignored;
- `retries_forever`: a refused model call is retried without end.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REFERENCE = Path(__file__).resolve().parents[3] / "examples" / "hosted" / "reference"
sys.path.insert(0, str(REFERENCE))

import agent  # noqa: E402 - the reference agent, from the path above
import responses_server  # noqa: E402

ROGUE = os.environ.get("ROGUE", "")
_reference_answer = agent.answer


def _tunnel(host: str) -> None:
    proxy = urlsplit(os.environ["HTTPS_PROXY"])
    with socket.create_connection((proxy.hostname or "", proxy.port or 0), timeout=5) as connection:
        connection.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
        connection.recv(1024)


def answer(text: str) -> str:
    if ROGUE == "direct_model":
        os.environ["OPENAI_BASE_URL"] = os.environ["CONFORMANCE_OUTSIDE_MODEL"]
        os.environ["OPENAI_API_KEY"] = "sk-the-agents-own-key"
    elif ROGUE == "no_tools":
        os.environ["CHOREGOS_MCP_SERVERS"] = ""
    elif ROGUE == "stray_egress":
        _tunnel("telemetry.example.com")
    elif ROGUE == "retries_forever":
        while True:
            try:
                return _reference_answer(text)
            except RuntimeError:
                time.sleep(0.2)
    return _reference_answer(text)


agent.answer = answer  # type: ignore[assignment]

if ROGUE == "never_ready":

    def _never_ready(self: Any) -> None:
        self._send(503, "application/json", json.dumps({"status": "starting"}).encode())

    responses_server.Handler.do_GET = _never_ready  # type: ignore[method-assign]

if __name__ == "__main__":
    if ROGUE == "ignores_sigterm":
        _signal = signal.signal

        def _ignoring(signum: int, handler: Any) -> Any:
            return _signal(signum, signal.SIG_IGN if signum == signal.SIGTERM else handler)

        signal.signal = _ignoring  # type: ignore[assignment]
    responses_server.main()
