# SPDX-License-Identifier: Apache-2.0
"""A warm runner waits for its run (`choregos-runner await-assignment`, ADR 0016, ADR 0053).

In a warm sandbox the runner starts BEFORE any run exists: no run id, no token, no git
identity. It listens on one port and answers two things:

- `GET /healthz` → 200: the sandbox is Ready, the controller may hand it out;
- `POST /assign` with `{run_id, api_url, run_token, env}` → 200 once, for one run.

The same run pushed again (a lost answer, a retried poll) is answered 200 again; another run is
answered 409, for the life of the process: a sandbox serves one run, never two. The token is
kept in memory only; nothing here logs it or writes it to disk.

Who may push? The pod accepts the first push; the executor sends it only to the pod of the
sandbox the controller bound to that run's claim. A pod of the namespace that pushed first
would gain a sandbox running ITS token — nothing it did not already hold — and the executor's own
push would then be answered 409 and the run failed, not shared.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

#: Bigger than any honest assignment (a token and an environment), small enough to refuse junk.
MAX_BODY = 64 * 1024


@dataclass
class Assignment:
    run_id: str
    api_url: str
    run_token: str
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, body: bytes) -> Assignment:
        data: Any = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("an assignment is a JSON object")
        missing = [key for key in ("run_id", "api_url", "run_token") if not str(data.get(key) or "")]
        if missing:
            raise ValueError(f"missing: {', '.join(missing)}")
        env = data.get("env", {})
        env = {} if env is None else env
        if not isinstance(env, dict):
            raise ValueError("`env` is an object of strings")
        return cls(
            run_id=str(data["run_id"]),
            api_url=str(data["api_url"]),
            run_token=str(data["run_token"]),
            env={str(k): str(v) for k, v in env.items()},
        )


class AssignmentServer:
    """The port a warm runner listens on. `wait()` returns the run it was given."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8765) -> None:  # noqa: S104 - the pod's own port
        self.host = host
        self.port = port
        self.assignment: Assignment | None = None
        self._given = asyncio.Event()
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        sockets = list(self._server.sockets)
        if sockets:
            self.port = int(sockets[0].getsockname()[1])

    async def wait(self) -> Assignment:
        await self._given.wait()
        assert self.assignment is not None
        return self.assignment

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            status, body = await self._answer(reader)
        except Exception:
            status, body = 400, {"error": "bad request"}
        payload = json.dumps(body).encode()
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 409: "Conflict", 413: "Too Large"}
        writer.write(
            f"HTTP/1.1 {status} {reason.get(status, 'Error')}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
            + payload
        )
        try:
            await writer.drain()
        finally:
            writer.close()

    async def _answer(self, reader: asyncio.StreamReader) -> tuple[int, dict[str, Any]]:
        request_line = (await asyncio.wait_for(reader.readline(), timeout=10)).decode("latin-1")
        method, path, *_ = request_line.split(" ")
        length = 0
        while True:
            line = (await asyncio.wait_for(reader.readline(), timeout=10)).decode("latin-1").strip()
            if not line:
                break
            name, _, value = line.partition(":")
            if name.strip().lower() == "content-length":
                length = int(value.strip())
        if method == "GET" and path == "/healthz":
            return 200, {"assigned": self.assignment is not None}
        if method != "POST" or path != "/assign":
            return 404, {"error": "not found"}
        if length > MAX_BODY:
            return 413, {"error": "assignment too large"}
        assignment = Assignment.parse(await asyncio.wait_for(reader.readexactly(length), timeout=10))
        if self.assignment is not None:
            if self.assignment.run_id == assignment.run_id:
                return 200, {"run_id": assignment.run_id}
            return 409, {"error": "this sandbox already serves another run"}
        self.assignment = assignment
        self._given.set()
        return 200, {"run_id": assignment.run_id}
