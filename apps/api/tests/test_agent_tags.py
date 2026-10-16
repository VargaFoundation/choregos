"""Agent tags (S40-10): labels people find an agent by, outside its versions."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from httpx import AsyncClient

from .agent_scopes_kit import (
    ADMIN,
    ORG,
    add_member,
    as_user,
    create_agent,
    install_common,
    make_project,
)

DEV = "dev@varga.dev"
OTHER = "other@varga.dev"
OWNER = "owner@varga.dev"


async def _tags(client: AsyncClient, **params: Any) -> list[str]:
    response = await client.get(f"{ORG}/agents", params=params)
    assert response.status_code == 200, response.text
    return [agent["slug"] for agent in response.json()]


async def test_an_agent_is_created_with_its_tags(client: AsyncClient, org: str) -> None:
    await as_user(client, ADMIN)
    created = await create_agent(client, "architect-x", tags=["madr", "design"])
    assert created.status_code == 201, created.text
    assert created.json()["tags"] == ["madr", "design"]
    assert (await client.get(f"{ORG}/agents/architect-x")).json()["tags"] == ["madr", "design"]
    assert (await create_agent(client, "plain")).json()["tags"] == []


@pytest.mark.parametrize(
    "tags",
    [["Design"], ["a b"], ["-x"], ["x" * 33], [f"t{n}" for n in range(11)], ["madr", "madr"]],
    ids=["uppercase", "space", "leading-dash", "33-chars", "eleven-tags", "duplicate"],
)
async def test_a_tag_must_be_a_lowercase_slug(client: AsyncClient, org: str, tags: list[str]) -> None:
    await as_user(client, ADMIN)
    assert (await create_agent(client, "bad-tags", tags=tags)).status_code == 422
    await create_agent(client, "good-tags")
    patched = await client.patch(f"{ORG}/agents/good-tags", json={"tags": tags})
    assert patched.status_code == 422
    assert (await client.get(f"{ORG}/agents/good-tags")).json()["tags"] == []


async def test_patch_replaces_the_tags_and_leaves_versions_alone(client: AsyncClient, org: str) -> None:
    await as_user(client, ADMIN)
    await create_agent(client, "architect-x", tags=["madr"])
    before = (await client.get(f"{ORG}/agents/architect-x")).json()
    patched = await client.patch(
        f"{ORG}/agents/architect-x", json={"tags": ["madr", "architecture", "design"]}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["tags"] == ["madr", "architecture", "design"]
    after = (await client.get(f"{ORG}/agents/architect-x")).json()
    assert after["latest_version"] == before["latest_version"] == 1
    assert [v["checksum"] for v in after["versions"]] == [v["checksum"] for v in before["versions"]]
    # absent or null leaves them; an empty list clears them
    await client.patch(f"{ORG}/agents/architect-x", json={"description": "d"})
    await client.patch(f"{ORG}/agents/architect-x", json={"tags": None})
    assert (await client.get(f"{ORG}/agents/architect-x")).json()["tags"] == [
        "madr",
        "architecture",
        "design",
    ]
    await client.patch(f"{ORG}/agents/architect-x", json={"tags": []})
    assert (await client.get(f"{ORG}/agents/architect-x")).json()["tags"] == []
    audit = (await client.get("/api/v1/audit", params={"target_type": "agent"})).json()["items"]
    updates = [e for e in audit if e["action"] == "agent.update"]
    assert ["madr", "architecture", "design"] in [e["payload"].get("tags") for e in updates]


async def test_only_who_may_publish_changes_tags(client: AsyncClient, org: str) -> None:
    await make_project(client, "dev")
    await add_member(client, DEV, "developer")
    await add_member(client, OWNER, "project_owner", project="dev")
    await as_user(client, ADMIN)
    await install_common(client)
    await create_agent(client, "common-org")
    await as_user(client, DEV)
    for slug in ("developer", "common-org"):
        refused = await client.patch(f"{ORG}/agents/{slug}", json={"tags": ["mine"]})
        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"]
    await as_user(client, OWNER)
    made = await create_agent(client, "dev-agent", scope="project", project="dev")
    assert made.status_code == 201, made.text
    ok = await client.patch(f"{ORG}/agents/dev-agent", json={"tags": ["billing"]})
    assert ok.status_code == 200 and ok.json()["tags"] == ["billing"]


async def test_the_registry_filters_by_every_tag_given(client: AsyncClient, org: str) -> None:
    await as_user(client, ADMIN)
    await create_agent(client, "one", tags=["design", "madr"])
    await create_agent(client, "two", tags=["design"])
    await create_agent(client, "three", tags=["other"])
    assert await _tags(client) == ["one", "three", "two"]
    assert await _tags(client, tag="design") == ["one", "two"]
    assert await _tags(client, tag=["design", "madr"]) == ["one"]
    assert await _tags(client, tag="nothing") == []


async def test_the_tag_filter_never_reveals_an_invisible_agent(client: AsyncClient, org: str) -> None:
    await add_member(client, DEV, "developer")
    await add_member(client, OTHER, "developer")
    await as_user(client, OTHER)
    assert (await create_agent(client, "secret", scope="personal", tags=["madr"])).status_code == 201
    await as_user(client, ADMIN)
    await create_agent(client, "public", tags=["madr"])
    await as_user(client, DEV)
    assert await _tags(client, tag="madr") == ["public"]
    await as_user(client, OTHER)
    assert await _tags(client, tag="madr") == ["public", "secret"]


async def test_a_fork_copies_the_tags(client: AsyncClient, org: str) -> None:
    await as_user(client, ADMIN)
    await create_agent(client, "source", tags=["madr", "design"])
    forked = await client.post(f"{ORG}/agents/source/fork", json={"slug": "copy", "scope": "org"})
    assert forked.status_code == 201, forked.text
    assert forked.json()["tags"] == ["madr", "design"]
    await client.patch(f"{ORG}/agents/copy", json={"tags": ["mine"]})
    assert (await client.get(f"{ORG}/agents/source")).json()["tags"] == ["madr", "design"]


async def test_the_catalogue_installs_the_architect_with_its_tags(client: AsyncClient, org: str) -> None:
    await as_user(client, ADMIN)
    entry = next(e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "architect")
    assert entry["tags"] == ["madr", "architecture", "design"]
    installed = await client.post(f"{ORG}/agent-catalogue/architect/install")
    assert installed.status_code == 201, installed.text
    assert installed.json()["tags"] == ["madr", "architecture", "design"]
    assert await _tags(client, tag=["madr", "design"]) == ["architect"]


async def test_an_upgrade_adds_catalogue_tags_and_keeps_the_organisation_s(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_core import catalogue_d_agents
    from choregos_core.catalogue_d_agents import entree, entrees

    await as_user(client, ADMIN)
    assert (await client.post(f"{ORG}/agent-catalogue/triager/install")).status_code == 201
    await client.patch(f"{ORG}/agents/triager", json={"tags": ["mine"]})
    old = entree("triager")
    assert old is not None
    new = dataclasses.replace(old, tags=("madr", "new"))
    others = tuple(e for e in entrees() if e.slug != "triager")
    monkeypatch.setattr(catalogue_d_agents, "entrees", lambda: (*others, new))
    upgraded = await client.post(f"{ORG}/agent-catalogue/triager/install", json={"upgrade": True})
    assert upgraded.status_code == 200, upgraded.text
    body = upgraded.json()
    assert body["tags"] == ["mine", "madr", "new"], "added, nothing removed"
    assert body["latest_version"] == 1, "a tag-only change publishes no version"

    own = [f"own-{n}" for n in range(10)]
    await client.patch(f"{ORG}/agents/triager", json={"tags": own})
    again = await client.post(f"{ORG}/agent-catalogue/triager/install", json={"upgrade": True})
    assert again.status_code == 200, again.text
    assert again.json()["tags"] == own, "at the cap, nothing is added and nothing fails"


async def test_the_spec_never_carries_tags() -> None:
    from choregos_api.schemas import AgentSpec

    assert "tags" not in AgentSpec.model_fields
