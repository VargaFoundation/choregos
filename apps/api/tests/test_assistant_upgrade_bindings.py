# SPDX-License-Identifier: Apache-2.0
"""A plugin upgrade that changes the needs of an assistant (S63-22).

A need dropped disables its binding and keeps it; a need added is unbound and a readiness item; a need whose
operations, access or identity changed is checked again against its connector (it follows the manifest when it
fits, it is disabled with an item when it does not); a flag `required` that moved changes the readiness. The
owners are told through the installation's email notifier.
"""

# ruff: noqa: F811 - the `offline` fixture imported from the kit is redefined by the parameter that names it

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_adapters.fakes.notify import FakeNotifier
from httpx import AsyncClient

from .agent_scopes_kit import as_user
from .kits.assistant_install import (
    BANK,
    FIXTURE,
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

SLUG = f"{ORG}/assistants/investment-assistant"


def _publish(
    root: Path, version: str, change: Callable[[list[dict[str, Any]]], None], drop: str | None = None
) -> None:
    target = root / "investment-assistant" / version
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(FIXTURE, target)
    manifest = target / "plugin.yaml"
    manifest.write_text(manifest.read_text().replace("version: 0.1.0", f"version: {version}"))
    path = target / "assistants" / "investment-assistant.yaml"
    document = yaml.safe_load(path.read_text())
    change(document["tools"])
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    if drop is not None:  # the agent may call only the needs the assistant declares
        agent = target / "agents" / "investment-analyst.yaml"
        text = agent.read_text()
        agent.write_text("".join(line for line in text.splitlines(True) if f"connector: {drop}," not in line))


def _tool(tools: list[dict[str, Any]], need: str) -> dict[str, Any]:
    return next(t for t in tools if t["need"] == need)


@pytest.fixture
def told() -> Iterator[FakeNotifier]:
    from choregos_api.services.task_delivery import set_notifier_factory

    notifier = FakeNotifier()
    set_notifier_factory(lambda row, channel: notifier if channel == "email" else None)
    yield notifier
    set_notifier_factory(None)


async def _ready(client: AsyncClient, *, bank: bool = False, news_modes: list[str] | None = None) -> None:
    await _plugin(client)
    assert (await _install(client, owner_groups=[OWNERS])).status_code == 201
    await _member(client, "owner@varga.dev", [OWNERS])
    await as_user(client, "admin@varga.dev")
    await _connector("news-feed", NEWS, identity_modes=news_modes)
    await _connector("markets", MARKET)
    assert (await _bind(client, "news", "news-feed")).status_code == 200
    assert (await _bind(client, "market_data", "markets")).status_code == 200
    if bank:
        await _connector("bank", BANK, identity_modes=["user_consent"])
        assert (await _bind(client, "bank_accounts", "bank")).status_code == 200
    assert (await client.get(f"{SLUG}/readiness")).json()["status"] == "ready"


async def _upgrade(client: AsyncClient, version: str) -> None:
    response = await client.post(f"{ORG}/plugins/investment-assistant/upgrade", json={"version": version})
    assert response.status_code == 200, response.text


async def _bindings(client: AsyncClient) -> dict[str, dict[str, Any]]:
    body = (await client.get(SLUG)).json()
    return {b["need"]: b for b in body["bindings"]}


async def test_a_need_the_manifest_dropped_has_its_binding_disabled_and_kept(
    client: AsyncClient, org: str, offline: Path, told: FakeNotifier
) -> None:
    await _ready(client, bank=True)
    _publish(offline, "0.2.0", lambda tools: tools.remove(_tool(tools, "bank_accounts")), "bank_accounts")
    await _upgrade(client, "0.2.0")
    kept = (await _bindings(client))["bank_accounts"]
    assert kept["enabled"] is False and kept["connector"] == "bank"
    assert (await client.get(f"{SLUG}/readiness")).json()["status"] == "ready"
    assert [to for to, _ in told.sent] == ["owner@varga.dev"]
    assert "`bank_accounts` is no longer a need" in told.sent[0][1].body


async def test_a_need_added_is_unbound_and_a_readiness_item(
    client: AsyncClient, org: str, offline: Path, told: FakeNotifier
) -> None:
    await _ready(client)
    _publish(
        offline,
        "0.2.0",
        lambda tools: tools.append(
            {
                "need": "fx",
                "operations": ["get_rate"],
                "access": "read",
                "identity": "service",
                "required": True,
            }
        ),
    )
    await _upgrade(client, "0.2.0")
    assert await _kinds(client) == [("need_unbound", "fx")]
    assert (await client.get(f"{SLUG}/readiness")).json()["status"] == "incomplete"
    assert "`fx` is a new required need" in told.sent[0][1].body


async def test_widened_operations_that_the_connector_serves_are_followed(
    client: AsyncClient, org: str, offline: Path, told: FakeNotifier
) -> None:
    await _ready(client)
    await _connector("archive", ("search_news", "get_article", "search_archive"))
    assert (await _bind(client, "news", "archive")).status_code == 200
    _publish(offline, "0.2.0", lambda tools: _tool(tools, "news")["operations"].append("search_archive"))
    await _upgrade(client, "0.2.0")
    news = (await _bindings(client))["news"]
    assert news["enabled"] is True and "search_archive" in news["operations"]
    assert (await client.get(f"{SLUG}/readiness")).json()["status"] == "ready"


async def test_widened_operations_the_connector_does_not_serve_disable_the_binding(
    client: AsyncClient, org: str, offline: Path, told: FakeNotifier
) -> None:
    await _ready(client)
    _publish(offline, "0.2.0", lambda tools: _tool(tools, "news")["operations"].append("search_archive"))
    await _upgrade(client, "0.2.0")
    assert (await _bindings(client))["news"]["enabled"] is False
    assert await _kinds(client) == [("need_not_served", "news")]
    assert (await client.get(f"{SLUG}")).json()["status"] == "incomplete"
    assert "does not serve `search_archive`" in told.sent[0][1].body


async def test_an_access_that_changed_is_checked_against_the_connector(
    client: AsyncClient, org: str, offline: Path, told: FakeNotifier
) -> None:
    await _ready(client, bank=True)
    _publish(
        offline,
        "0.2.0",
        lambda tools: _tool(tools, "bank_accounts").update(access="write", approval="console"),
    )
    await _upgrade(client, "0.2.0")
    assert (await _bindings(client))["bank_accounts"]["enabled"] is False
    assert (await client.get(f"{SLUG}")).json()[
        "status"
    ] == "ready"  # an optional need: switched off, not missing
    assert "serves `list_accounts` as `read`" in told.sent[0][1].body


async def test_an_identity_the_connector_cannot_carry_disables_the_binding(
    client: AsyncClient, org: str, offline: Path, told: FakeNotifier
) -> None:
    await _ready(client)
    _publish(offline, "0.2.0", lambda tools: _tool(tools, "news").update(identity="user_consent"))
    await _upgrade(client, "0.2.0")
    assert (await _bindings(client))["news"]["enabled"] is False
    assert await _kinds(client) == [("identity_unsupported", "news")]


async def test_an_identity_the_connector_carries_is_followed(
    client: AsyncClient, org: str, offline: Path, told: FakeNotifier
) -> None:
    await _ready(client, news_modes=["user_consent"])
    _publish(offline, "0.2.0", lambda tools: _tool(tools, "news").update(identity="user_consent"))
    await _upgrade(client, "0.2.0")
    news = (await _bindings(client))["news"]
    assert news["enabled"] is True and news["identity"] == "user_consent"
    assert (await client.get(f"{SLUG}/readiness")).json()["status"] == "ready"


async def test_a_required_flag_that_moved_changes_the_readiness(
    client: AsyncClient, org: str, offline: Path, told: FakeNotifier
) -> None:
    await _ready(client)
    _publish(offline, "0.2.0", lambda tools: _tool(tools, "brokerage").update(required=True))
    await _upgrade(client, "0.2.0")
    assert await _kinds(client) == [("need_unbound", "brokerage")]
    assert "`brokerage` is now required" in told.sent[0][1].body
    told.sent.clear()
    _publish(offline, "0.3.0", lambda tools: _tool(tools, "brokerage").update(required=False))
    await _upgrade(client, "0.3.0")
    assert await _kinds(client) == []
    assert "`brokerage` is now optional" in told.sent[0][1].body


async def test_an_upgrade_that_changes_no_need_says_nothing(
    client: AsyncClient, org: str, offline: Path, told: FakeNotifier
) -> None:
    await _ready(client)
    _publish(offline, "0.2.0", lambda tools: None)
    await _upgrade(client, "0.2.0")
    assert told.sent == [] and (await _bindings(client))["news"]["enabled"] is True
