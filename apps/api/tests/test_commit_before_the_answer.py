"""A request's transaction is committed BEFORE the response leaves, not after.

By default FastAPI closes `yield` dependencies AFTER the response is sent. `get_db` committed its
transaction there: a client received its `201`, then the database wrote. Two consequences, measured
on the trial's integrated stack (uvicorn, PostgreSQL):
- a fast client reads a state that is not yet written: right after the development sign-in, the next
  request did not find the user: "stale session";
- a `commit` that fails afterwards leaves the client with a success response for a lost write.

`ASGITransport` waits for the application to finish before returning the response: it saw nothing.
This test calls the ASGI application directly and looks at the order of the events.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_the_commit_precedes_the_sending_of_the_response(
    app: Any, client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    events: list[str] = []
    original = AsyncSession.commit

    async def commit(self: AsyncSession) -> None:
        events.append("commit")
        await original(self)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    body = json.dumps({"name": "order", "expires_in_days": 1}).encode()
    cookie = f"choregos_session={client.cookies.get('choregos_session')}".encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/me/tokens",
        "raw_path": b"/api/v1/me/tokens",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), (b"content-type", b"application/json"), (b"cookie", cookie)],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    was_read = False

    async def receive() -> dict[str, Any]:
        nonlocal was_read
        if not was_read:
            was_read = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    statuses: list[int] = []

    async def send(message: dict[str, Any]) -> None:
        events.append(message["type"])
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    await app(scope, receive, send)
    assert statuses == [201], events
    assert "commit" in events
    assert events.index("commit") < events.index("http.response.start"), events
