"""A WebSocket client that drives the ASGI application in the test's own event loop.

Starlette's `TestClient` runs the application in another thread and another loop, where the test's
database engine and fakes do not live. This one speaks the ASGI WebSocket messages directly.
"""

from __future__ import annotations

import asyncio
import json
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

from httpx import AsyncClient

TIMEOUT_S = 5.0


class Closed(Exception):  # noqa: N818 - the server closed the socket, as a test expects it to
    def __init__(self, code: int, reason: str = "") -> None:
        super().__init__(f"closed {code}: {reason}")
        self.code = code
        self.reason = reason


class AsgiWebSocket:
    def __init__(self, app: Any, url: str, headers: dict[str, str] | None = None) -> None:
        parts = urlsplit(url)
        self.app = app
        self.scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "server": ("test", 80),
            "client": ("127.0.0.1", 50000),
            "root_path": "",
            "path": parts.path,
            "raw_path": parts.path.encode(),
            "query_string": parts.query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "subprotocols": [],
        }
        self._to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.close_code: int | None = None

    async def __aenter__(self) -> AsgiWebSocket:
        self._task = asyncio.create_task(self.app(self.scope, self._to_app.get, self._from_app.put))
        await self._to_app.put({"type": "websocket.connect"})
        first = await self._next()
        if first["type"] == "websocket.close":
            await self._finish()
            raise Closed(first.get("code", 1000), first.get("reason") or "")
        assert first["type"] == "websocket.accept", first
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self.close_code is None:
            await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        await self._finish()

    async def _next(self) -> dict[str, Any]:
        assert self._task is not None
        getter = asyncio.ensure_future(self._from_app.get())
        done, _ = await asyncio.wait(
            {getter, self._task}, timeout=TIMEOUT_S, return_when=asyncio.FIRST_COMPLETED
        )
        if getter in done:
            return getter.result()
        getter.cancel()
        if self._task in done:
            self._task.result()  # raises what the application raised
            return {"type": "websocket.close", "code": 1006, "reason": "the application ended"}
        raise TimeoutError("the server sent nothing")

    async def _finish(self) -> None:
        if self._task is not None:
            await asyncio.wait_for(self._task, TIMEOUT_S)

    async def send_text(self, text: str) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def send_json(self, event: dict[str, Any]) -> None:
        await self.send_text(json.dumps(event))

    async def receive_json(self) -> dict[str, Any]:
        message = await self._next()
        if message["type"] == "websocket.close":
            self.close_code = message.get("code", 1000)
            raise Closed(self.close_code, message.get("reason") or "")
        assert message["type"] == "websocket.send", message
        loaded: dict[str, Any] = json.loads(message["text"])
        return loaded

    async def until(self, type_: str) -> dict[str, Any]:
        """The next event of that type, the ones before it skipped."""
        while True:
            event = await self.receive_json()
            if event.get("type") == type_:
                return event

    async def closed(self) -> Closed:
        """Reads until the server closes; the close."""
        while True:
            try:
                await self.receive_json()
            except Closed as closed:
                await self._finish()
                return closed

    async def hang_up(self) -> None:
        self.close_code = 1000
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        await self._finish()

    async def drop(self, *, wait: bool = True) -> None:
        """The socket goes away without a close frame (1006), as a lost network leaves it; without
        `wait`, the server's handler is left to finish on its own (a hosted one waits for a resume)."""
        self.close_code = 1006
        await self._to_app.put({"type": "websocket.disconnect", "code": 1006})
        if wait:
            await self._finish()


def cookie_header(client: AsyncClient) -> dict[str, str]:
    """The signed-in session of an httpx test client, as a browser's socket would carry it."""
    return {"cookie": "; ".join(f"{name}={value}" for name, value in client.cookies.items())}
