"""What the console reads of a manifest (S63-18): a plugin's assistants and a user's assistant pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from .conftest import login
from .kits.assistant_install import (
    MARKET,
    NEWS,
    ORG,
    _bind,
    _connector,
    _install,
    _member,
    _plugin,
    offline,  # noqa: F401
)


async def test_a_plugin_lists_its_assistants_with_their_config_schema(
    client: AsyncClient,
    org: str,
    offline: Path,  # noqa: F811
) -> None:
    await _plugin(client)
    found = await client.get(f"{ORG}/plugins/investment-assistant/assistants")
    assert found.status_code == 200, found.text
    (assistant,) = found.json()["items"]
    assert assistant["name"] == "investment-assistant"
    assert assistant["config_schema"]["required"] == ["house_name"]
    assert assistant["suggested_groups"] == ["grp-investors"]
    assert {n["need"] for n in assistant["needs"]} >= {"news", "market_data", "orders"}
    assert (await client.get(f"{ORG}/plugins/nothing/assistants")).status_code == 404


async def test_my_assistants_carry_their_memory_spaces_tasks_and_needs(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
) -> None:
    await _member(client, "reader@varga.dev", ["investors"])
    await _plugin(client)
    await _install(client, audience_groups=["investors"])
    await _connector("news-feed", NEWS)
    await _connector("markets", MARKET)
    await _bind(client, "news", "news-feed")
    await _bind(client, "market_data", "markets")
    reader = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(reader, "reader@varga.dev")
    mine = await reader.get("/api/v1/me/assistants")
    assert mine.status_code == 200, mine.text
    (item,) = mine.json()["items"]
    assert {s["name"] for s in item["memory_spaces"]} == {
        "investor_profile",
        "preferred_sources",
        "instrument_notes",
    }
    assert {t["name"]: t["schedulable"] for t in item["tasks"]} == {
        "morning_brief": True,
        "threshold_alert": True,
    }
    assert any(n["need"] == "bank_accounts" and not n["required"] for n in item["needs"])


async def test_a_conversation_names_its_organisation_and_its_assistant(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
) -> None:
    await _member(client, "reader@varga.dev", ["investors"])
    await _plugin(client)
    await _install(client, audience_groups=["investors"])
    await _connector("news-feed", NEWS)
    await _connector("markets", MARKET)
    await _bind(client, "news", "news-feed")
    await _bind(client, "market_data", "markets")
    reader = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(reader, "reader@varga.dev")
    opened = await reader.post("/api/v1/me/assistants/varga/investment-assistant/conversations", json={})
    assert opened.status_code == 201, opened.text
    assert (opened.json()["org"], opened.json()["assistant"]) == ("varga", "investment-assistant")
    # A conversation read from anywhere (the list, a link) says the same: no `?assistant=` is needed.
    read = await reader.get(f"/api/v1/me/conversations/{opened.json()['id']}")
    assert (read.json()["org"], read.json()["assistant"]) == ("varga", "investment-assistant")
    (row,) = (await reader.get("/api/v1/me/conversations")).json()["items"]
    assert (row["org"], row["assistant"]) == ("varga", "investment-assistant")
