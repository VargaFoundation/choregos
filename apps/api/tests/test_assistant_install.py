"""Installing an assistant, binding its needs, readiness (S63-02, ADR 0133).

Each guard has its test: an unbound required need leaves the installation `incomplete` with one readiness
item, binding it makes it `ready`; a connector that does not serve an operation, serves it with another
access or cannot carry the identity is refused with a 422 and nothing changes; a required configuration
key without a value is a readiness item; install and uninstall are the administrator's, configuring and
binding the owner groups' too; uninstalling the plugin suspends its assistants.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from .conftest import login
from .kits.assistant_install import (
    BANK,
    MARKET,
    NEWS,
    ORG,
    OWNERS,
    _bind,
    _connector,
    _install,
    _kinds,
    _member,
    _plugin,
    offline,  # noqa: F401
)


async def test_an_unbound_required_need_is_incomplete_and_binding_it_makes_it_ready(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
) -> None:
    await _plugin(client)
    installed = await _install(client)
    assert installed.status_code == 201, installed.text
    assert installed.json()["status"] == "incomplete"
    assert installed.json()["display_name"] == "Investment assistant"
    assert await _kinds(client) == [("need_unbound", "news"), ("need_unbound", "market_data")]

    await _connector("news-feed", NEWS)
    await _connector("markets", MARKET)
    first = await _bind(client, "news", "news-feed")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "incomplete"
    assert await _kinds(client) == [("need_unbound", "market_data")]

    second = await _bind(client, "market_data", "markets")
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "ready"
    assert {b["need"] for b in second.json()["bindings"]} == {"news", "market_data"}
    assert (await client.get(f"{ORG}/assistants/investment-assistant/readiness")).json() == {
        "status": "ready",
        "items": [],
    }
    # An optional need left unbound is not a gap.
    assert (await client.get(f"{ORG}/assistants")).json()["items"][0]["status"] == "ready"


async def test_a_connector_that_does_not_serve_the_operation_is_refused(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
) -> None:
    await _plugin(client)
    await _install(client)
    await _connector("partial-news", ("search_news",))
    refused = await _bind(client, "news", "partial-news")
    assert refused.status_code == 422, refused.text
    assert "get_article" in refused.text
    after = (await client.get(f"{ORG}/assistants/investment-assistant")).json()
    assert after["bindings"] == [] and after["status"] == "incomplete"


async def test_an_access_that_differs_is_refused(client: AsyncClient, org: str, offline: Path) -> None:  # noqa: F811
    await _plugin(client)
    await _install(client)
    await _connector("writing-news", NEWS, access="write")
    refused = await _bind(client, "news", "writing-news")
    assert refused.status_code == 422, refused.text
    assert "as `write`" in refused.text


async def test_an_identity_the_connector_cannot_carry_is_refused(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
) -> None:
    from choregos_api.services.connector_identity import supports_identity

    await _plugin(client)
    await _install(client)
    await _connector("plain-bank", BANK)
    refused = await _bind(client, "bank_accounts", "plain-bank")
    assert refused.status_code == 422, refused.text
    assert "user_consent" in refused.text
    await _connector("consent-bank", BANK, identity_modes=["user_consent"])
    accepted = await _bind(client, "bank_accounts", "consent-bank")
    assert accepted.status_code == 200, accepted.text
    # The one function: `service` always, the others only when declared.
    from choregos_api.db.models import OrgConnector

    assert supports_identity(OrgConnector(config={}), "service")
    assert not supports_identity(OrgConnector(config={}), "user_exchange")
    assert supports_identity(OrgConnector(config={"identity_modes": ["user_exchange"]}), "user_exchange")
    assert not supports_identity(OrgConnector(config={"identity_modes": ["user_exchange"]}), "user_consent")


async def test_a_connector_that_loses_an_operation_makes_it_incomplete_again(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
) -> None:
    from choregos_api.db.models import ConnectorOperation
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete

    await _plugin(client)
    await _install(client)
    await _connector("news-feed", NEWS)
    await _connector("markets", MARKET)
    await _bind(client, "news", "news-feed")
    await _bind(client, "market_data", "markets")
    async with session_scope() as session:
        await session.execute(delete(ConnectorOperation).where(ConnectorOperation.name == "get_article"))
    assert await _kinds(client) == [("need_not_served", "news")]
    assert (await client.get(f"{ORG}/assistants/investment-assistant")).json()["status"] == "incomplete"


async def test_a_required_config_key_without_a_value_is_a_readiness_item(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
) -> None:
    await _plugin(client)
    installed = await client.post(
        f"{ORG}/assistants", json={"plugin": "investment-assistant", "assistant": "investment-assistant"}
    )
    assert installed.status_code == 201, installed.text
    assert ("config_missing", "house_name") in await _kinds(client)
    patched = await client.patch(
        f"{ORG}/assistants/investment-assistant", json={"config": {"house_name": "V"}}
    )
    assert patched.status_code == 200, patched.text
    assert ("config_missing", "house_name") not in await _kinds(client)
    wrong = await client.patch(f"{ORG}/assistants/investment-assistant", json={"config": {"house_name": 3}})
    assert wrong.status_code == 422, wrong.text


async def test_install_refusals(client: AsyncClient, org: str, offline: Path) -> None:  # noqa: F811
    await _plugin(client)
    unknown = await _install(client, assistant="nothing")
    assert unknown.status_code == 404
    assert (await _install(client)).status_code == 201
    assert (await _install(client)).status_code == 409
    assert (await _install(client, slug="second")).status_code == 201
    need = await client.put(f"{ORG}/assistants/second/bindings/ghost", json={"connector": "x"})
    assert need.status_code == 404
    unknown_connector = await _bind(client, "news", "no-such-connector", slug="second")
    assert unknown_connector.status_code == 422
    off = await client.put(
        f"{ORG}/assistants/second/bindings/news", json={"connector": "x", "enabled": False}
    )
    assert off.status_code == 422


async def test_rights(client: AsyncClient, org: str, app: Any, offline: Path) -> None:  # noqa: F811
    await _plugin(client)
    await _member(client, "owner@varga.dev", [OWNERS])
    await _member(client, "other@varga.dev", ["somebody-else"])
    await _connector("news-feed", NEWS)
    assert (await _install(client, owner_groups=[OWNERS])).status_code == 201

    async def session_of(email: str) -> AsyncClient:
        other = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        await login(other, email)
        return other

    owner = await session_of("owner@varga.dev")
    other = await session_of("other@varga.dev")
    assert (await _install(owner, slug="mine")).status_code == 403
    assert (await _bind(owner, "news", "news-feed")).status_code == 200
    assert (
        await owner.patch(f"{ORG}/assistants/investment-assistant", json={"channel": "candidate"})
    ).status_code == 200
    assert (
        await owner.patch(f"{ORG}/assistants/investment-assistant", json={"owner_groups": ["x"]})
    ).status_code == 403
    assert (await owner.delete(f"{ORG}/assistants/investment-assistant")).status_code == 403
    assert [i["slug"] for i in (await owner.get(f"{ORG}/assistants")).json()["items"]] == [
        "investment-assistant"
    ]

    assert (await _bind(other, "news", "news-feed")).status_code == 403
    assert (await other.get(f"{ORG}/assistants/investment-assistant")).status_code == 403
    assert (await other.get(f"{ORG}/assistants/investment-assistant/readiness")).status_code == 403
    assert (await other.get(f"{ORG}/assistants")).json()["items"] == []

    assert (await client.delete(f"{ORG}/assistants/investment-assistant")).status_code == 204
    assert (await client.get(f"{ORG}/assistants/investment-assistant")).status_code == 404


async def test_uninstalling_the_plugin_suspends_its_assistants(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
) -> None:
    await _plugin(client)
    await _install(client)
    gone = await client.delete(f"{ORG}/plugins/investment-assistant")
    assert gone.status_code == 200, gone.text
    shown = (await client.get(f"{ORG}/assistants/investment-assistant")).json()
    assert shown["status"] == "suspended"
    await _plugin(client)
    assert (await client.get(f"{ORG}/assistants/investment-assistant")).json()["status"] == "incomplete"
