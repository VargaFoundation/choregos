"""The adds that came with the catalogues and remote agents open their host first (S39-22a, ADR 0066)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters.fakes.a2a import FakeA2aAgent
from httpx import AsyncClient

from .conftest_remote import remote_agent, set_sealing_key
from .egress_support import ORG, add_member, as_person, rules_in_db
from .test_remote_agents import _body
from .test_skill_catalogue import fetcher  # noqa: F401  (the recorded repositories)

TOKEN = {"token": "env:CONNECTOR_CATALOGUE_TEST"}


@pytest.fixture
def enforce(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeA2aAgent]:
    set_sealing_key(monkeypatch)
    yield from remote_agent()


async def test_an_mcp_catalogue_install_asks_for_the_servers_host_then_records_the_entry(
    client: AsyncClient, admin: str, enforce: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONNECTOR_CATALOGUE_TEST", "vendor-key")
    url = f"{ORG}/mcp-catalogue/github/install"
    asked = await client.post(url, json={"secret_refs": TOKEN})
    assert asked.status_code == 409 and asked.json()["code"] == "egress_required", asked.text
    assert asked.json()["verdicts"][0]["host"] == "api.githubcopilot.com"
    assert (await client.get(f"{ORG}/connectors")).json() == []
    done = await client.post(url, json={"secret_refs": TOKEN, "allow_egress": True})
    assert done.status_code == 201, done.text
    (rule,) = await rules_in_db("allowed")
    assert rule.host == "api.githubcopilot.com" and rule.origin.startswith("mcp-catalogue:github@")


async def test_a_git_skill_catalogue_entry_opens_its_repository_hosts(
    client: AsyncClient,
    admin: str,
    enforce: None,
    fetcher: Any,  # noqa: F811
) -> None:
    url = f"{ORG}/skill-catalogue/systematic-debugging/install"
    asked = await client.post(url)
    assert asked.status_code == 409 and asked.json()["code"] == "egress_required", asked.text
    assert {v["host"] for v in asked.json()["verdicts"]} == {"api.github.com", "codeload.github.com"}
    done = await client.post(url, json={"allow_egress": True})
    assert done.status_code == 201, done.text
    assert {r.origin for r in await rules_in_db("allowed")} == {"skill-catalogue:systematic-debugging"}


async def test_a_bundled_skill_needs_no_network(client: AsyncClient, admin: str, enforce: None) -> None:
    installed = await client.post(f"{ORG}/skill-catalogue/write-spec/install")
    assert installed.status_code == 201, installed.text
    assert await rules_in_db() == []


async def test_a_remote_agent_opens_its_card_host_for_the_api_and_the_runners(
    client: AsyncClient, admin: str, remote: FakeA2aAgent, enforce: None
) -> None:
    await client.post(f"{ORG}/remote-credentials", json={"name": "rovo", "type": "none"})
    remote.security = "none"
    body = _body(remote, credential=None)
    body["spec"]["runtime"].pop("credential")
    await add_member("root@varga.dev", "org_admin")
    asked = await client.post(f"{ORG}/agents", json=body)
    assert asked.status_code == 409 and asked.json()["code"] == "egress_required", asked.text
    assert {(v["host"], v["used_by"]) for v in asked.json()["verdicts"]} == {
        ("a2a.example.test", "api"),
        ("a2a.example.test", "runners"),
    } or len(asked.json()["verdicts"]) == 2
    async with as_person(client, "owner@varga.dev", "project_owner") as owner:
        assert (await owner.post(f"{ORG}/agents?allow_egress=true", json=body)).status_code == 403
    done = await client.post(f"{ORG}/agents?allow_egress=true", json=body)
    assert done.status_code == 201, done.text
    (rule,) = await rules_in_db("allowed")
    assert rule.used_by == ["api", "runners"] and rule.origin.startswith("remote-agent:")


def test_the_deprecated_api_hosts_fold_into_the_api_ceiling() -> None:
    from choregos_api.config import Settings
    from choregos_api.services.egress import ceiling_of

    assert ceiling_of(Settings(api_egress_hosts=["*.notion.com"])) == {"api": ("*.notion.com",)}
    both = Settings(api_egress_hosts=["a.test"], egress_ceiling={"api": ["b.test"]})
    assert ceiling_of(both) == {"api": ("b.test",)}, "the ceiling wins"
