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
    assert bearer.startswith("chg_")
    # The cookies stay on the client: the Bearer header alone must exempt the request.
    assert client.cookies
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


async def test_only_a_bearer_header_exempts_the_cookie(client: AsyncClient, admin: str) -> None:
    before = await _token_count()
    for authorization in ("Basic Zm9vOmJhcg==", ""):
        refused = await client.post(
            TOKENS,
            json={"name": "x"},
            headers={"Authorization": authorization, "Origin": "https://evil.example"},
        )
        assert refused.status_code == 403, authorization
    assert await _token_count() == before


async def test_safe_methods_are_not_guarded(client: AsyncClient, admin: str) -> None:
    answer = await client.get(TOKENS, headers={"Origin": "https://evil.example"})
    assert answer.status_code == 200, answer.text


async def test_origins_are_compared_normalised(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for public_url, origin in (
        ("https://app.example.com:443", "https://app.example.com"),
        ("https://App.Example.com", "https://app.example.com"),
        ("http://app.example.com:80", "http://app.example.com"),
        ("https://app.example.com", "HTTPS://APP.EXAMPLE.COM:443"),
    ):
        monkeypatch.setenv("CHOREGOS_PUBLIC_URL", public_url)
        reset_settings_cache()
        ok = await client.post(TOKENS, json={"name": "x"}, headers={"Origin": origin})
        assert ok.status_code == 201, (public_url, origin, ok.text)
    other_port = await client.post(
        TOKENS, json={"name": "x"}, headers={"Origin": "https://app.example.com:8443"}
    )
    assert other_port.status_code == 403
    reset_settings_cache()


async def test_an_empty_origin_falls_back_to_sec_fetch_site(client: AsyncClient, admin: str) -> None:
    refused = await client.post(
        TOKENS, json={"name": "x"}, headers={"Origin": "", "Sec-Fetch-Site": "cross-site"}
    )
    assert refused.status_code == 403
    passed = await client.post(TOKENS, json={"name": "y"}, headers={"Origin": ""})
    assert passed.status_code == 201, passed.text


async def test_sec_fetch_site_is_case_insensitive(client: AsyncClient, admin: str) -> None:
    refused = await client.post(TOKENS, json={"name": "x"}, headers={"Sec-Fetch-Site": "CROSS-SITE"})
    assert refused.status_code == 403


async def _preflight(client: AsyncClient, method: str, headers: str) -> int:
    answer = await client.options(
        "/healthz",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )
    return answer.status_code


async def test_the_preflight_allows_only_listed_headers_and_methods(client: AsyncClient) -> None:
    assert await _preflight(client, "POST", "content-type") == 200
    assert await _preflight(client, "POST", "x-evil") == 400
    assert await _preflight(client, "TRACE", "content-type") == 400


async def test_the_console_origin_can_read_the_request_id(client: AsyncClient) -> None:
    answer = await client.get("/healthz", headers={"Origin": "http://localhost:3000"})
    assert "x-request-id" in answer.headers["access-control-expose-headers"].lower()


async def test_the_correlation_id_prefers_the_one_the_logging_middleware_set() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-request-id", b"from-the-header")],
        "query_string": b"",
        "state": {"request_id": "from-the-middleware"},
    }
    assert errors.correlation_id(Request(scope)) == "from-the-middleware"
