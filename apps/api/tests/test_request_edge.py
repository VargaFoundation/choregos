"""The API's edge: cookie mutations from another origin, CORS, and the client's request id (S47-03)."""

from __future__ import annotations

import pytest
from choregos_api import errors
from choregos_api.config import reset_settings_cache
from choregos_api.db.models import ApiToken
from choregos_api.db.session import session_scope
from choregos_api.main import create_app
from httpx import AsyncClient
from sqlalchemy import func, select
from starlette.requests import Request

pytestmark = pytest.mark.asyncio

TOKENS = "/api/v1/me/tokens"


async def _token_count() -> int:
    async with session_scope() as db:
        return int((await db.execute(select(func.count()).select_from(ApiToken))).scalar_one())


async def test_a_foreign_origin_cannot_use_the_session_cookie(client: AsyncClient, admin: str) -> None:
    before = await _token_count()
    for origin in ("https://evil.example", "null"):
        refused = await client.post(TOKENS, json={"name": "x"}, headers={"Origin": origin})
        assert refused.status_code == 403, origin
        assert refused.headers["content-type"].startswith("application/problem+json")
        assert refused.json()["status"] == 403
    assert await _token_count() == before


async def test_a_cross_site_request_without_origin_is_refused(client: AsyncClient, admin: str) -> None:
    for site in ("cross-site", "same-site"):
        refused = await client.post(TOKENS, json={"name": "x"}, headers={"Sec-Fetch-Site": site})
        assert refused.status_code == 403, site


async def test_the_console_origin_the_cli_and_bearer_tokens_pass(client: AsyncClient, admin: str) -> None:
    console = await client.post(TOKENS, json={"name": "x"}, headers={"Origin": "http://localhost:3000"})
    assert console.status_code == 201, console.text
    cli = await client.post(TOKENS, json={"name": "y"})
    assert cli.status_code == 201, cli.text
    bearer = cli.json()["token"]
    client.cookies.clear()
    api = await client.post(
        TOKENS,
        json={"name": "z"},
        headers={"Authorization": f"Bearer {bearer}", "Origin": "https://evil.example"},
    )
    assert api.status_code == 201, api.text


async def test_cors_wildcard_stops_the_start(app: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_CORS_ORIGINS", "*")
    reset_settings_cache()
    with pytest.raises(RuntimeError, match="CHOREGOS_CORS_ORIGINS"):
        create_app()


async def test_an_oversized_or_odd_request_id_is_replaced(client: AsyncClient) -> None:
    for odd in ("a" * 5000, "x y<z>"):
        answer = await client.get("/healthz", headers={"X-Request-Id": odd})
        got = answer.headers["x-request-id"]
        assert got != odd
        assert 0 < len(got) <= 64
    kept = await client.get("/healthz", headers={"X-Request-Id": "req-du-front-42"})
    assert kept.headers["x-request-id"] == "req-du-front-42"


async def test_the_correlation_id_never_reflects_a_raw_header() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-request-id", b"a" * 100)],
        "query_string": b"",
    }
    got = errors.correlation_id(Request(scope))
    assert len(got) == 16
    assert int(got, 16) >= 0
