"""The RFC 8693 client against the fake identity provider (S63-07, ADR 0135)."""

from __future__ import annotations

import pytest
from choregos_adapters.fakes.identity_provider import ENDPOINT, FakeIdentityProvider
from choregos_adapters.token_exchange import (
    GRANT_EXCHANGE,
    TOKEN_TYPE_ACCESS,
    ExchangeRefused,
    TokenExchangeClient,
    claims_of,
)


def _client(idp: FakeIdentityProvider) -> TokenExchangeClient:
    return TokenExchangeClient(
        ENDPOINT, client_id="choregos", client_secret="s3cret", transport=idp.transport()
    )


async def test_the_exchange_sends_the_rfc_8693_request_and_returns_the_persons_token() -> None:
    idp = FakeIdentityProvider(access_tokens={"person-token": "sub-1"})
    issued = await _client(idp).exchange("person-token", audience="iris-mcp", actor="assistant:prep")
    (form,) = idp.requests
    assert form["grant_type"] == GRANT_EXCHANGE and form["subject_token"] == "person-token"
    assert (
        form["subject_token_type"] == TOKEN_TYPE_ACCESS and form["requested_token_type"] == TOKEN_TYPE_ACCESS
    )
    assert form["audience"] == "iris-mcp" and form["client_id"] == "choregos"
    claims = claims_of(issued.access_token)
    assert (
        claims["sub"] == "sub-1"
        and claims["aud"] == "iris-mcp"
        and claims["act"] == {"sub": "assistant:prep"}
    )
    assert issued.access_token not in repr(issued), "a token never reaches a log through repr"


async def test_a_refusal_carries_the_providers_code_and_never_a_token() -> None:
    idp = FakeIdentityProvider(access_tokens={"person-token": "sub-1"}, allowed_audiences={"other"})
    with pytest.raises(ExchangeRefused) as refused:
        await _client(idp).exchange("person-token", audience="iris-mcp")
    assert refused.value.code == "access_denied" and "person-token" not in str(refused.value)
    with pytest.raises(ExchangeRefused) as unknown:
        await _client(idp).exchange("not-a-token", audience="other")
    assert unknown.value.code == "invalid_token"


async def test_the_refresh_grant_draws_an_access_token_and_reports_a_rotation() -> None:
    idp = FakeIdentityProvider(refresh_tokens={"rt": "sub-1"}, rotate=True)
    drawn = await _client(idp).refresh("rt")
    assert idp.access_tokens[drawn.access_token] == "sub-1"
    assert drawn.refresh_token is not None and drawn.refresh_token != "rt"
    with pytest.raises(ExchangeRefused):
        await _client(idp).refresh("revoked")
