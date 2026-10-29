"""The Agent Card: parsed (Atlassian's real one), digested, fetched under the SSRF rules (ADR 0062)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest
from choregos_adapters.a2a import AUTH_API_KEY, AUTH_OAUTH, CardError, digest_of, fetch_card, parse_card
from choregos_adapters.fakes.a2a import FakeA2aAgent

ROVO = json.loads((Path(__file__).parent / "fixtures" / "a2a" / "rovo-agent-card.json").read_text())


def test_atlassians_rovo_card_parses() -> None:
    """The card copied from https://a2a.atlassian.com/.well-known/agent.json (2026-10-15)."""
    card = parse_card(ROVO)
    assert card.url == "https://a2a.atlassian.com/v1/rovo"
    assert card.protocol_version == "0.3.0" and card.major == 0
    assert [s.id for s in card.skills] == ["work-in-jira", "work-in-confluence"]
    assert card.auth_types == (AUTH_OAUTH,)
    assert card.token_url == "https://auth.atlassian.com/oauth/token"
    assert card.authorization_url == "https://auth.atlassian.com/authorize"
    assert set(card.scopes) == {"read:me", "offline_access", "full_access:chat:rovo"}
    assert card.dcr_url and card.dcr_url.startswith("https://auth.atlassian.com/")
    assert card.streaming is True
    assert card.host == "a2a.atlassian.com"


def test_the_digest_is_stable_and_moves_with_any_change() -> None:
    assert digest_of(ROVO) == digest_of(copy.deepcopy(ROVO))
    assert digest_of(ROVO) == parse_card(ROVO).digest
    # key order is not content
    assert digest_of({"a": 1, "b": 2}) == digest_of({"b": 2, "a": 1})
    changed = copy.deepcopy(ROVO)
    changed["skills"][0]["description"] += "."
    assert digest_of(changed) != digest_of(ROVO)
    assert digest_of(ROVO).startswith("sha256:")


def test_a_card_without_an_endpoint_is_refused() -> None:
    with pytest.raises(CardError):
        parse_card({"name": "x"})


def test_a_card_that_declares_no_security_accepts_none() -> None:
    card = parse_card(FakeA2aAgent(security="none").card_document())
    assert card.auth_types == ("none",)
    assert parse_card(FakeA2aAgent(security="bearer").card_document()).auth_types == ("bearer",)


def test_an_api_key_in_a_header_is_accepted_and_the_header_named() -> None:
    card = parse_card(FakeA2aAgent(security="api_key", api_key_header="x-sn-apikey").card_document())
    assert card.auth_types == (AUTH_API_KEY,)
    assert card.api_key_headers == ("x-sn-apikey",)
    assert card.takes_api_key_in("X-SN-APIKEY"), "header names are case-insensitive"
    assert not card.takes_api_key_in("X-API-Key")


def test_an_api_key_in_the_1_0_shape_is_read_too() -> None:
    raw = FakeA2aAgent(security="none").card_document()
    raw["securitySchemes"] = {"key": {"apiKeySecurityScheme": {"location": "header", "name": "X-API-Key"}}}
    raw["security"] = [{"key": []}]
    card = parse_card(raw)
    assert card.auth_types == (AUTH_API_KEY,) and card.api_key_headers == ("X-API-Key",)


@pytest.mark.parametrize("where", ["query", "cookie", ""])
def test_a_key_outside_a_header_is_not_supported(where: str) -> None:
    """A key in a query string lands in every access log on the way; a cookie is a browser's."""
    raw = FakeA2aAgent(security="none").card_document()
    raw["securitySchemes"] = {"key": {"type": "apiKey", "in": where, "name": "api_key"}}
    raw["security"] = [{"key": []}]
    card = parse_card(raw)
    assert card.auth_types == () and card.api_key_headers == ()


async def test_fetch_reads_the_card_over_the_transport() -> None:
    fake = FakeA2aAgent()
    card = await fetch_card(fake.card_url, transport=fake.transport())
    assert card.url == fake.endpoint and fake.card_fetches == 1


async def test_a_plain_http_card_is_refused() -> None:
    fake = FakeA2aAgent(host="http://agent.remote.test")
    with pytest.raises(CardError, match="https"):
        await fetch_card(fake.card_url, transport=fake.transport())


async def test_a_redirect_to_another_host_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/card.json"})

    with pytest.raises(CardError, match="another host"):
        await fetch_card(
            "https://agent.remote.test/.well-known/agent.json", transport=httpx.MockTransport(handler)
        )


async def test_a_card_that_is_not_json_or_not_200_is_refused() -> None:
    with pytest.raises(CardError, match="404"):
        await fetch_card("https://a.test/c", transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(CardError, match="not JSON"):
        await fetch_card(
            "https://a.test/c", transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))
        )
