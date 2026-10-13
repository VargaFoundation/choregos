# SPDX-License-Identifier: Apache-2.0
"""A warm runner waits for its run (`choregos-runner await-assignment`, ADR 0016, ADR 0053).

In a warm sandbox the runner starts BEFORE any run exists: no run id, no token, no git
identity. It makes an X25519 key pair in memory, prints the public key on one line of its stdout
(the executor reads it through the API server, `pods/log`), listens on one port and answers:

- `GET /healthz` → 200: the sandbox is Ready, the controller may hand it out;
- `POST /assign` with `{run_id, sealed}` → 200 once, for one run. `sealed` is the run's token,
  API URL and environment, sealed to THIS pod's key and bound to `run_id`
  (`choregos_adapters.sealing`): nothing on the wire is readable but the run id.

What does not open with this pod's key for this run id is refused (400) and assigns nothing.
Once an assignment opened, the private key is dropped: the pod can open nothing else. The same
run pushed again (a lost answer, a retried poll) is answered 200; another run 409, for the life
of the process — a sandbox serves one run, never two. The token stays in memory only; nothing
here logs it or writes it to disk.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from choregos_adapters.sealing import SealError, key_line, new_key, open_sealed

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

#: Bigger than any honest assignment (a token and an environment), small enough to refuse junk.
MAX_BODY = 64 * 1024


@dataclass
class Assignment:
    run_id: str
    api_url: str
    run_token: str
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_opened(cls, run_id: str, data: dict[str, Any]) -> Assignment:
        missing = [key for key in ("api_url", "run_token") if not str(data.get(key) or "")]
        if missing or not run_id:
            raise ValueError(f"missing: {', '.join(missing or ['run_id'])}")
        env = data.get("env", {})
        env = {} if env is None else env
        if not isinstance(env, dict):
            raise ValueError("`env` is an object of strings")
        return cls(
            run_id=run_id,
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
        private, self.public_key = new_key()
        self._private: X25519PrivateKey | None = private
        self._given = asyncio.Event()
        self._server: asyncio.Server | None = None

    @property
    def key_line(self) -> str:
        """The line to print at boot, before listening: what the executor seals to."""
        return key_line(self.public_key)

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
        data: Any = json.loads(await asyncio.wait_for(reader.readexactly(length), timeout=10))
        if not isinstance(data, dict):
            return 400, {"error": "an assignment is a JSON object"}
        run_id, sealed = str(data.get("run_id") or ""), str(data.get("sealed") or "")
        if self.assignment is not None:
            if self.assignment.run_id == run_id:
                return 200, {"run_id": run_id}
            return 409, {"error": "this sandbox already serves another run"}
        if self._private is None or not run_id or not sealed:
            return 400, {"error": "a sealed assignment and its run id are required"}
        try:
            assignment = Assignment.from_opened(run_id, open_sealed(self._private, run_id, sealed))
        except (SealError, ValueError):
            return 400, {"error": "the assignment is not sealed to this pod for this run"}
        self._private = None  # one assignment, ever: nothing else can be opened
        self.assignment = assignment
        self._given.set()
        return 200, {"run_id": run_id}
