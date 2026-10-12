"""An unhandled error is answered as problem+json with a correlation id (S26-02).

Before, Starlette answered a plain-text "Internal Server Error": the console showed "Internal Server
Error" and nothing tied it to the API's log line.
"""

from __future__ import annotations

from typing import Any

from httpx import ASGITransport, AsyncClient
from starlette.types import ASGIApp, Receive, Scope, Send


async def _boom() -> dict[str, str]:
    raise RuntimeError("secret-token-abc leaked into an exception message")


async def test_a_route_that_raises_answers_problem_json_with_a_correlation_id(app: Any) -> None:
    app.add_api_route("/api/v1/s26-boom", _boom)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/api/v1/s26-boom", headers={"X-Request-Id": "req-s26-02"})
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["status"] == 500 and problem["title"] == "Internal server error"
    assert problem["correlation_id"] == "req-s26-02" == response.headers["X-Request-Id"]
    assert "req-s26-02" in problem["detail"]
    assert "secret-token" not in response.text, "the exception message stays in the logs"


async def test_a_generated_correlation_id_is_the_one_returned_in_the_header(app: Any) -> None:
    app.add_api_route("/api/v1/s26-boom", _boom)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/api/v1/s26-boom")
    problem = response.json()
    assert problem["correlation_id"] and problem["correlation_id"] == response.headers["X-Request-Id"]


class _FailsOutsideTheLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == "/api/v1/s26-outer":
            raise RuntimeError("outer failure")
        await self.app(scope, receive, send)


async def test_a_failure_outside_the_logging_middleware_is_still_problem_json(app: Any) -> None:
    app.add_middleware(_FailsOutsideTheLoggingMiddleware)
    # Starlette re-raises after answering, so the server logs the traceback: not an app error here.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/api/v1/s26-outer", headers={"X-Request-Id": "req-outer"})
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["correlation_id"] == "req-outer"
