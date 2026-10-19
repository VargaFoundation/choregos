"""The organisation's network policy is ONE model with two halves (ADR 0070, amended at the
integration of S39-22): the hosts it OPENS (its egress allowlist, ADR 0066) and the BOUNDS nothing
may cross (its policy ceiling's `network`). A deny always wins: over an opening, at every call the
platform makes (in every egress mode), and over the hosts opened for every run."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import pytest
from httpx import AsyncClient

from .egress_support import ORG, as_person, rules_in_db
from .test_skill_catalogue import fetcher  # noqa: F401  (the recorded repositories)

CEILING = f"{ORG}/policy-ceiling"


async def _ceiling(client: AsyncClient, network: dict[str, Any]) -> None:
    done = await client.put(CEILING, json={"ceiling": {"network": network}})
    assert done.status_code == 200, done.text


async def test_opening_a_host_the_ceiling_denies_is_a_422_and_opens_nothing(
    client: AsyncClient, admin: str
) -> None:
    await _ceiling(client, {"deny_domains": ["blocked.example.test"]})
    refused = await client.post(f"{ORG}/egress/rules", json={"host": "api.blocked.example.test"})
    assert refused.status_code == 422, refused.text
    assert "denied:blocked.example.test" in refused.text and "Administration" in refused.text
    wide = await client.post(f"{ORG}/egress/rules", json={"host": "*.blocked.example.test"})
    assert wide.status_code == 422, "a wildcard the deny covers is refused too"
    assert await rules_in_db() == []
    assert (await client.post(f"{ORG}/egress/rules", json={"host": "fine.example.test"})).status_code == 201


async def test_approving_a_request_the_ceiling_denies_is_a_422_and_the_request_waits(
    client: AsyncClient, admin: str
) -> None:
    async with as_person(client, "dev@varga.dev", "developer") as dev:
        asked = await dev.post(f"{ORG}/egress/requests", json={"host": "blocked.example.test"})
        assert asked.status_code == 201, asked.text
    await _ceiling(client, {"allow_domains": ["vendor.example"]})
    approved = await client.post(f"{ORG}/egress/requests/{asked.json()['id']}/approve", json={})
    assert approved.status_code == 422 and "not_allowed" in approved.text, approved.text
    assert [r.status for r in await rules_in_db()] == ["requested"]


async def test_a_card_on_a_denied_host_is_never_fetched_whatever_the_egress_mode(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the fakes the egress mode is `warn`: the list alone would only log. The bounds ride in
    the same policy as the list, in the guarded transport, and refuse before the request is sent."""
    from choregos_adapters.fakes.a2a import FakeA2aAgent

    from .conftest_remote import remote_agent, set_sealing_key

    set_sealing_key(monkeypatch)
    for remote in remote_agent():
        assert isinstance(remote, FakeA2aAgent)
        remote.security = "none"
        discover = {"card_url": remote.card_url}
        assert (await client.post(f"{ORG}/agents/discover", json=discover)).status_code == 200
        fetched = remote.card_fetches
        await _ceiling(client, {"deny_domains": [urlsplit(remote.card_url).hostname or ""]})
        refused = await client.post(f"{ORG}/agents/discover", json=discover)
        assert refused.status_code == 422, refused.text
        assert "network policy" in refused.text and "denied:" in refused.text
        assert remote.card_fetches == fetched, "the card was never requested"


async def test_the_hosts_opened_for_runners_join_the_project_network_under_the_deny_lists(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """What the egress proxy and the console read is what the orchestrator writes into a run."""
    for host in ("a2a.example.test", "*.mirror.example.test", "api.example.test"):
        used_by = ["api"] if host == "api.example.test" else ["runners"]
        created = await client.post(f"{ORG}/egress/rules", json={"host": host, "used_by": used_by})
        assert created.status_code == 201, created.text
    read = (await client.get(f"/api/v1/projects/{project['id']}/network")).json()
    assert {"a2a.example.test", "mirror.example.test"} <= set(read["effective"]["implied"])
    assert "api.example.test" not in read["effective"]["implied"], "an `api` rule is not a runner's"
    await _ceiling(client, {"deny_domains": ["a2a.example.test"]})
    read = (await client.get(f"/api/v1/projects/{project['id']}/network")).json()
    assert "a2a.example.test" not in read["effective"]["implied"]
    assert "mirror.example.test" in read["effective"]["implied"]


async def test_a_git_skill_catalogue_entry_on_a_denied_host_is_a_422_before_any_opening(
    client: AsyncClient,
    admin: str,
    fetcher: Any,  # noqa: F811
) -> None:
    """Like a git import (S40-01d): the bounds first, then the egress gate's 409."""
    await _ceiling(client, {"deny_domains": ["github.com"]})
    url = f"{ORG}/skill-catalogue/systematic-debugging/install"
    refused = await client.post(url, json={"allow_egress": True})
    assert refused.status_code == 422 and "denied:github.com" in refused.text, refused.text
    assert await rules_in_db() == [], "nothing was opened"
