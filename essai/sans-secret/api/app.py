# SPDX-License-Identifier: Apache-2.0
"""Trial API stub (element 8): the bootstrap rules of contract 03 §12.1, nothing else.

- ``POST /internal/runs`` and ``POST /internal/runs/{id}/pod``: the orchestrator registers a run (hash
  of its single-use code, declared secrets) and the IP and start time of its pod.
- ``POST /runs/{id}/bootstrap``: the run exchanges its code for a token and its secrets. Order of the
  checks: unknown code 401, replay 409 (revokes the token, fails the run), pod not yet recorded 425
  (retryable), expired 410, IP mismatch 403.
- ``POST /runs/{id}/result``: with the run token; 401 once the token is revoked.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INTERNAL_TOKEN = os.environ["ESSAI_INTERNAL_TOKEN"]
VALIDITY_SECONDS = 600
VAULT = {"DEMO_TOKEN": secrets.token_hex(16)}  # stands in for the core's vault
RUNS: dict[str, dict] = {}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict:
        length = int(self.headers.get("content-length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def log_message(self, fmt: str, *args: object) -> None:
        print("api:", fmt % args, flush=True)

    def do_GET(self) -> None:
        parts = self.path.strip("/").split("/")
        if parts[:2] == ["internal", "runs"] and len(parts) == 3:
            if not self._internal():
                return
            run = RUNS.get(parts[2])
            if run is None:
                return self._send(404, {"error": "unknown run"})
            view = {k: v for k, v in run.items() if k not in {"code_sha256", "token"}}
            return self._send(200, view)
        return self._send(404, {"error": "not found"})

    def _internal(self) -> bool:
        if not hmac.compare_digest(self.headers.get("x-internal-token", ""), INTERNAL_TOKEN):
            self._send(401, {"error": "internal token required"})
            return False
        return True

    def do_POST(self) -> None:  # noqa: C901 — one branch per route of the stub
        parts = self.path.strip("/").split("/")
        body = self._body()
        if parts == ["internal", "runs"]:
            if not self._internal():
                return
            RUNS[body["run_id"]] = {"code_sha256": body["code_sha256"], "secrets": body.get("secrets", []),
                                    "status": "queued", "pod_ip": None, "start_time": None,
                                    "consumed_at": None, "consumed_ip": None, "token": None, "revoked": False}
            return self._send(201, {"ok": True})
        if parts[:2] == ["internal", "runs"] and len(parts) == 4 and parts[3] == "pod":
            if not self._internal():
                return
            run = RUNS[parts[2]]
            run["pod_ip"], run["start_time"] = body["ip"], body["start_time"]
            return self._send(200, {"ok": True})
        if parts[0] == "runs" and len(parts) == 3 and parts[2] == "bootstrap":
            return self._bootstrap(parts[1], body)
        if parts[0] == "runs" and len(parts) == 3 and parts[2] == "result":
            run = RUNS.get(parts[1])
            presented = self.headers.get("authorization", "").removeprefix("Bearer ")
            if run is None or run["token"] is None or run["revoked"] or not hmac.compare_digest(presented, run["token"]):
                return self._send(401, {"error": "run token invalid or revoked"})
            run["status"] = "succeeded" if body.get("exit_code") == 0 else "failed"
            run["result"] = body
            return self._send(204, {})
        return self._send(404, {"error": "not found"})

    def _bootstrap(self, run_id: str, body: dict) -> None:
        run = RUNS.get(run_id)
        presented = hashlib.sha256(str(body.get("code", "")).encode()).hexdigest()
        if run is None or not hmac.compare_digest(presented, run["code_sha256"]):
            return self._send(401, {"error": "bootstrap_code_invalid"})
        caller = self.client_address[0]
        if run["consumed_at"] is not None:
            run["revoked"], run["status"] = True, "failed"
            run["failure"] = "bootstrap_replayed"
            print(f"api: event dev.koinon.run.bootstrap_replayed run={run_id} caller={caller}", flush=True)
            return self._send(409, {"error": "bootstrap_code_consumed"})
        if run["pod_ip"] is None or run["start_time"] is None:
            # The pod may call before the orchestrator has recorded its IP: retryable, never final.
            return self._send(425, {"error": "bootstrap_not_ready", "retry_after": 1})
        if time.time() > run["start_time"] + VALIDITY_SECONDS:
            return self._send(410, {"error": "bootstrap_code_expired"})
        if caller != run["pod_ip"]:
            return self._send(403, {"error": "bootstrap_ip_mismatch"})
        run["consumed_at"], run["consumed_ip"], run["status"] = time.time(), caller, "running"
        run["token"] = secrets.token_urlsafe(32)
        return self._send(200, {"token": run["token"], "secrets": {n: VAULT[n] for n in run["secrets"]}})


if __name__ == "__main__":
    print("api: listening on :8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104 — in-cluster stub
