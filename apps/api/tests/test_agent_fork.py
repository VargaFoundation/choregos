"""Forking an agent (S39-09, ADR 0061): a copy of one version, its origin recorded, nothing else inherited."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .agent_scopes_kit import (
    ADMIN,
    ORG,
    SPEC,
    add_member,
    as_user,
    create_agent,
    install_common,
    make_project,
)

DEV = "dev@varga.dev"
OTHER = "other@varga.dev"
OWNER = "owner@varga.dev"


async def _three_versions(client: AsyncClient) -> None:
    await install_common(client)
    for text in ("Second.", "Third."):
        published = await client.post(f"{ORG}/agents/developer/versions", json={**SPEC, "instructions": text})
        assert published.status_code == 201, published.text


async def test_a_fork_copies_the_version_byte_for_byte_and_records_its_origin(
    client: AsyncClient, org: str
) -> None:
    await make_project(client, "dev")
    await add_member(client, OWNER, "project_owner", project="dev")
    await as_user(client, ADMIN)
    await _three_versions(client)
    source_before = (await client.get(f"{ORG}/agents/developer")).json()
    third = next(v for v in source_before["versions"] if v["version"] == 3)

    await as_user(client, OWNER)
    forked = await client.post(
        f"{ORG}/agents/developer/fork",
        json={"slug": "dev-developer", "scope": "project", "project": "dev", "version": 3},
    )
    assert forked.status_code == 201, forked.text
    fork = forked.json()
    assert (fork["scope"], fork["project"], fork["latest_version"]) == ("project", "dev", 1)
    assert fork["versions"][0]["spec"] == third["spec"]
    assert fork["versions"][0]["checksum"] == third["checksum"], "same bytes, same checksum"
    assert fork["forked_from"] == {
        "slug": "developer",
        "version": 3,
        "scope": "platform",
        "latest_version": 3,
        "behind": False,
        "available": True,
    }
    assert fork["can"]["publish"] is True, "the project owner owns the fork"

    await as_user(client, ADMIN)
    assert (await client.get(f"{ORG}/agents/developer")).json() == source_before, (
        "the source is never modified"
    )
    # nothing else is inherited: no pin, no metrics, no client
    assert (await client.get(f"{ORG}/agents/dev-developer/credentials")).json() == []


async def test_a_fork_reports_when_its_upstream_moved_and_defaults_to_the_latest_version(
    client: AsyncClient, org: str
) -> None:
    await install_common(client)
    await as_user(client, DEV)
    fork = await client.post(
        f"{ORG}/agents/developer/fork", json={"slug": "my-developer", "scope": "personal"}
    )
    assert fork.status_code == 201, fork.text
    assert fork.json()["forked_from"]["version"] == 1 and fork.json()["forked_from"]["behind"] is False

    await as_user(client, ADMIN)
    assert (
        await client.post(f"{ORG}/agents/developer/versions", json={**SPEC, "instructions": "v2"})
    ).status_code == 201
    await as_user(client, DEV)
    behind = (await client.get(f"{ORG}/agents/my-developer")).json()["forked_from"]
    assert (behind["behind"], behind["latest_version"], behind["version"]) == (True, 2, 1)
    # nothing re-bases by itself
    assert (await client.get(f"{ORG}/agents/my-developer")).json()["latest_version"] == 1
    assert [
        a["forked_from"]["behind"] for a in (await client.get(f"{ORG}/agents?scope=personal")).json()
    ] == [True]


async def test_a_fork_whose_source_is_gone_says_so(client: AsyncClient, org: str) -> None:
    from choregos_api.db.models import Agent
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await install_common(client)
    await as_user(client, DEV)
    assert (
        await client.post(f"{ORG}/agents/developer/fork", json={"slug": "my-developer", "scope": "personal"})
    ).status_code == 201
    async with session_scope() as session:
        source = (await session.execute(select(Agent).where(Agent.slug == "developer"))).scalar_one()
        await session.delete(source)
    upstream = (await client.get(f"{ORG}/agents/my-developer")).json()["forked_from"]
    assert upstream == {
        "slug": "developer",
        "version": 1,
        "scope": None,
        "latest_version": None,
        "behind": False,
        "available": False,
    }


async def test_who_may_fork_where(client: AsyncClient, org: str) -> None:
    await make_project(client, "dev")
    await add_member(client, OTHER, "developer")
    await install_common(client)
    await as_user(client, DEV)
    assert (await create_agent(client, "mine", scope="personal")).status_code == 201

    # a developer forks into their own agents, not into the organisation
    org_fork = await client.post(
        f"{ORG}/agents/developer/fork", json={"slug": "org-developer", "scope": "org"}
    )
    assert org_fork.status_code == 403 and "org_admin" in org_fork.text
    project_fork = await client.post(
        f"{ORG}/agents/developer/fork", json={"slug": "dev-developer", "scope": "project", "project": "dev"}
    )
    assert project_fork.status_code == 403

    # someone else's personal agent does not exist for them
    await as_user(client, OTHER)
    assert (
        await client.post(f"{ORG}/agents/mine/fork", json={"slug": "stolen", "scope": "personal"})
    ).status_code == 404

    # a slug that is taken: 409
    await as_user(client, DEV)
    clash = await client.post(f"{ORG}/agents/developer/fork", json={"slug": "mine", "scope": "personal"})
    assert clash.status_code == 409
    missing = await client.post(
        f"{ORG}/agents/developer/fork", json={"slug": "other", "scope": "personal", "version": 9}
    )
    assert missing.status_code == 404
    # `project` goes with a project fork, and only with it
    assert (
        await client.post(f"{ORG}/agents/developer/fork", json={"slug": "x-fork", "scope": "project"})
    ).status_code == 422
    assert (
        await client.post(
            f"{ORG}/agents/developer/fork", json={"slug": "x-fork", "scope": "personal", "project": "dev"}
        )
    ).status_code == 422
    # an administrator forks anywhere, and a fork of a fork records its immediate parent only
    await as_user(client, ADMIN)
    forked = await client.post(f"{ORG}/agents/mine/fork", json={"slug": "org-mine", "scope": "org"})
    assert forked.status_code == 201, forked.text
    assert forked.json()["forked_from"]["slug"] == "mine"
    grandchild: dict[str, Any] = (
        await client.post(f"{ORG}/agents/org-mine/fork", json={"slug": "org-mine-2", "scope": "org"})
    ).json()
    assert grandchild["forked_from"]["slug"] == "org-mine"
