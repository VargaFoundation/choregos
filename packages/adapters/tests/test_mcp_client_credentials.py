# SPDX-License-Identifier: Apache-2.0
"""A direct MCP server signed in with OAuth client credentials (RFC 6749 §4.4).

The connector is built as the platform builds it (`build("mcp", "mcp", …)` under CHOREGOS_FAKES, so the
server is the scriptable `FakeMcpServer` and the token endpoint the scriptable `FakeOAuthProvider`). Each
test fails without the guard it names: the token kept until shortly before it expires, a token the
server refuses fetched again once and only once, a wrong secret refused by name, the token endpoint held
to the organisation's egress list, the configuration checked before anything is saved.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import choregos_adapters
import httpx
import pytest
from choregos_adapters import build, egress_hosts, validate_config
from choregos_adapters.errors import AdapterError
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.fakes.oauth_provider import FakeOAuthProvider
from choregos_adapters.mcp import CONNECTOR_TOKENS, McpError, TokenRefused
from choregos_adapters.mcp import client_credentials as module
from choregos_core.egress import AllowedRule, EgressPolicy, EgressRefusedError, use_policy

TOOLS = [{"name": "read_orders", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}]
SECRET = "client-secret-of-the-reader"
CONFIG: dict[str, Any] = {
    "url": "https://data.example.test/mcp",
    "auth_scheme": "oauth_client_credentials",
    "token_url": "https://auth.example.test/oauth/token",
    "client_id": "choregos-reader",
    "scope": "mcp:read",
    "audience": "data-platform",
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeOAuthProvider]:
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    fake = FakeOAuthProvider(client_id="choregos-reader", client_secret=SECRET)
    monkeypatch.setattr(choregos_adapters, "FAKE_OAUTH", fake)
    CONNECTOR_TOKENS.clear()
    yield fake
    CONNECTOR_TOKENS.clear()


@pytest.fixture
def server(provider: FakeOAuthProvider, monkeypatch: pytest.MonkeyPatch) -> FakeMcpServer:
    fake = FakeMcpServer(tools=[dict(t) for t in TOOLS], accepts=provider.accepts)
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", fake)
    return fake


def _client(secret: str = SECRET, **over: Any) -> Any:
    return build("mcp", "mcp", {**CONFIG, "client_secret": secret, **over})


def _bearers(server: FakeMcpServer) -> list[str]:
    return [headers.get("authorization", "") for _, headers, _ in server.received]


async def test_a_token_is_fetched_once_with_the_client_and_reused(
    provider: FakeOAuthProvider, server: FakeMcpServer
) -> None:
    assert [t.name for t in await _client().list_tools()] == ["read_orders"]
    assert (await _client().call_tool("read_orders", {}))["content"]
    (grant,) = provider.client_grants()
    assert grant == {"grant_type": "client_credentials", "scope": "mcp:read", "audience": "data-platform"}
    assert "client_secret" not in grant, "the client authenticates with HTTP Basic, not in the form"
    (token,) = provider.client_tokens
    assert set(_bearers(server)) == {f"Bearer {token}"}, "every request carries the fetched token"
    assert all(SECRET not in str(headers) for _, headers, _ in server.received), "the secret stays home"


async def test_the_token_is_kept_until_shortly_before_it_expires(
    provider: FakeOAuthProvider, server: FakeMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(module, "_now", lambda: clock[0])
    await _client().list_tools()
    clock[0] += provider.client_token_ttl_s - module.TOKEN_SKEW_S - 1
    await _client().list_tools()
    assert len(provider.client_grants()) == 1, "still fresh: reused"
    clock[0] += 2
    await _client().list_tools()
    assert len(provider.client_grants()) == 2, "about to expire: renewed before the server refuses it"
    assert len(provider.client_tokens) == 2 and len(set(_bearers(server))) == 2


async def test_an_expired_token_is_fetched_again_once_after_the_401(
    provider: FakeOAuthProvider, server: FakeMcpServer
) -> None:
    await _client().list_tools()
    provider.expire_client_tokens()  # the provider's clock ran out; the platform still caches the token
    assert [t.name for t in await _client().list_tools()] == ["read_orders"]
    assert len(provider.client_grants()) == 2, "one new token after the server's 401"
    first, second = provider.client_tokens
    assert _bearers(server)[-1] == f"Bearer {second}" and first != second


async def test_a_server_that_refuses_every_token_is_an_error_not_a_loop(
    provider: FakeOAuthProvider, server: FakeMcpServer
) -> None:
    server.accepts = lambda _token: False
    with pytest.raises(McpError) as refused:
        await _client().list_tools()
    assert refused.value.code == 401
    assert len(provider.client_grants()) == 2, "the first token, then one renewal: never a third"


async def test_a_wrong_secret_is_a_named_refusal_that_carries_no_secret(
    provider: FakeOAuthProvider, server: FakeMcpServer
) -> None:
    with pytest.raises(TokenRefused) as refused:
        await _client(secret="a-wrong-secret").list_tools()
    assert refused.value.code == 401 and refused.value.oauth_error == "invalid_client"
    message = str(refused.value)
    assert "auth.example.test" in message and "choregos-reader" in message and "invalid_client" in message
    assert "a-wrong-secret" not in message and SECRET not in message
    assert isinstance(refused.value, McpError), "every caller already reports an McpError"
    assert server.received == [], "nothing reached the server without a token"


async def test_a_new_secret_never_reuses_the_token_of_the_former_one(
    provider: FakeOAuthProvider, server: FakeMcpServer
) -> None:
    await _client().list_tools()
    provider.client_secret = "the-rotated-secret"
    await _client(secret="the-rotated-secret").list_tools()
    assert len(provider.client_grants()) == 2


async def test_the_token_endpoint_is_held_to_the_egress_list(
    provider: FakeOAuthProvider, server: FakeMcpServer
) -> None:
    only_the_server = EgressPolicy(org="varga", rules=(AllowedRule("data.example.test"),), mode="enforce")
    with use_policy(only_the_server), pytest.raises(EgressRefusedError) as refused:
        await _client().list_tools()
    assert refused.value.host == "auth.example.test"
    assert isinstance(refused.value, httpx.HTTPError), "reported like an unreachable host"
    assert provider.requests == [], "the token request never left"
    assert server.received == [], "nor did the call it was for"
    both = EgressPolicy(
        org="varga",
        rules=(AllowedRule("data.example.test"), AllowedRule("auth.example.test")),
        mode="enforce",
    )
    with use_policy(both):
        assert [t.name for t in await _client().list_tools()] == ["read_orders"]


async def test_without_its_secret_the_connector_is_not_built(provider: FakeOAuthProvider) -> None:
    with pytest.raises(AdapterError, match="client_secret"):
        build("mcp", "mcp", dict(CONFIG))


async def test_bearer_and_basic_connectors_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    fake = FakeMcpServer(tools=[dict(t) for t in TOOLS], token="static-key")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", fake)
    client = build("mcp", "mcp", {"url": "https://data.example.test/mcp", "token": "static-key"})
    assert [t.name for t in await client.list_tools()] == ["read_orders"]


def test_the_token_endpoint_is_a_host_the_connector_opens() -> None:
    assert egress_hosts("mcp", "mcp", CONFIG) == [("data.example.test", 443), ("auth.example.test", 443)]
    bearer = {"url": "https://data.example.test/mcp"}
    assert egress_hosts("mcp", "mcp", bearer) == [("data.example.test", 443)]


@pytest.mark.parametrize(
    ("over", "refusal"),
    [
        ({"token_url": None}, "token_url"),
        ({"client_id": " "}, "client_id"),
        ({"token_url": "http://auth.example.test/oauth/token"}, "https"),
        ({"token_url": "https://user:pass@auth.example.test/token"}, "credentials"),
        ({"token_url": "ftp://auth.example.test/token"}, "https"),
        ({"scope": 3}, "scope"),
        ({"auth_scheme": "bearer"}, "belong to"),
        ({"auth_scheme": "digest"}, "unknown MCP auth scheme"),
    ],
    ids=[
        "no token endpoint",
        "no client id",
        "a token endpoint in plain http",
        "credentials in the token endpoint",
        "not an http address",
        "a scope that is not text",
        "oauth fields under bearer",
        "an unknown scheme",
    ],
)
def test_an_incoherent_configuration_is_refused_before_it_is_saved(
    over: dict[str, Any], refusal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    config = {key: value for key, value in {**CONFIG, **over}.items() if value is not None}
    with pytest.raises(AdapterError, match=refusal):
        validate_config("mcp", "mcp", config)


def test_a_coherent_configuration_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    validate_config("mcp", "mcp", CONFIG)
    validate_config("mcp", "mcp", {"url": "https://data.example.test/mcp", "auth_scheme": "basic"})
