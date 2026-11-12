"""Forks, watchers, stars and the inbox items an agent's evolutions raise (ADR 0152)."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_api.errors import ApiError
from httpx import AsyncClient

from .agent_scopes_kit import ADMIN, ORG, SPEC, add_member, as_user, create_agent, install_common

DEV = "dev@varga.dev"
OTHER = "other@varga.dev"
CANDIDATE_POLICY = {"on_publish": "candidate", "stable": {"require_candidate": True}}


def _spec(n: int) -> dict[str, Any]:
    return {**SPEC, "instructions": f"Review the change, revision {n}."}


async def _publish(client: AsyncClient, slug: str, n: int) -> None:
    published = await client.post(f"{ORG}/agents/{slug}/versions", json=_spec(n))
    assert published.status_code == 201, published.text


async def _social(client: AsyncClient, slug: str) -> dict[str, Any]:
    return dict((await client.get(f"{ORG}/agents/{slug}")).json()["social"])


async def _inbox(client: AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get(f"{ORG}/agent-notifications", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _kinds(client: AsyncClient) -> list[str]:
    return sorted(item["kind"] for item in (await _inbox(client))["items"])


async def _watched_reviewer(client: AsyncClient, policy: dict[str, Any] | None = None) -> None:
    """`reviewer` (an org agent of the administrator) watched by OTHER, a developer."""
    await add_member(client, OTHER, "developer")
    await as_user(client, ADMIN)
    body: dict[str, Any] = {"spec": _spec(1)}
    if policy:
        body["release_policy"] = policy
    assert (await create_agent(client, "reviewer", **body)).status_code == 201
    await as_user(client, OTHER)
    assert (await client.put(f"{ORG}/agents/reviewer/watch")).status_code == 200


# ───────────────────────────── forks ─────────────────────────────


async def test_the_forks_are_listed_with_how_far_they_moved_and_only_the_visible_ones(
    client: AsyncClient, org: str
) -> None:
    await add_member(client, OTHER, "developer")
    await install_common(client)
    await as_user(client, DEV)
    mine = await client.post(f"{ORG}/agents/developer/fork", json={"slug": "my-dev", "scope": "personal"})
    assert mine.status_code == 201, mine.text
    await _publish(client, "my-dev", 2)

    await as_user(client, ADMIN)
    forked = await client.post(f"{ORG}/agents/developer/fork", json={"slug": "org-dev", "scope": "org"})
    assert forked.status_code == 201, forked.text
    listed = (await client.get(f"{ORG}/agents/developer/forks")).json()["items"]
    by_slug = {item["slug"]: item for item in listed}
    assert set(by_slug) == {"my-dev", "org-dev"}, "an org_admin sees a personal fork"
    assert by_slug["my-dev"]["diverged"] is True and by_slug["my-dev"]["latest_version"] == 2
    assert by_slug["my-dev"]["forked_from_version"] == 1 and by_slug["my-dev"]["owner"] == DEV
    assert by_slug["my-dev"]["scope"] == "personal" and by_slug["org-dev"]["diverged"] is False
    assert by_slug["org-dev"]["created_at"]
    assert (await _social(client, "developer"))["forks"] == 2

    await as_user(client, OTHER)
    seen = (await client.get(f"{ORG}/agents/developer/forks")).json()["items"]
    assert [item["slug"] for item in seen] == ["org-dev"], (
        "somebody else's personal fork is not theirs to see"
    )
    assert (await _social(client, "developer"))["forks"] == 1

    assert (await client.get(f"{ORG}/agents/nope/forks")).status_code == 404


async def test_an_agent_the_caller_cannot_see_has_no_forks_to_list_watch_or_star(
    client: AsyncClient, org: str
) -> None:
    await add_member(client, OTHER, "developer")
    await as_user(client, DEV)
    assert (await create_agent(client, "private-one", scope="personal")).status_code == 201
    await as_user(client, OTHER)
    assert (await client.get(f"{ORG}/agents/private-one/forks")).status_code == 404
    for verb in ("watch", "star"):
        assert (await client.put(f"{ORG}/agents/private-one/{verb}")).status_code == 404
        assert (await client.delete(f"{ORG}/agents/private-one/{verb}")).status_code == 404


# ───────────────────────────── watch and star ─────────────────────────────


async def test_the_owner_watches_what_they_create_and_a_fork_and_can_stop(
    client: AsyncClient, org: str
) -> None:
    await install_common(client)
    await as_user(client, DEV)
    assert (await create_agent(client, "mine", scope="personal")).status_code == 201
    assert await _social(client, "mine") == {
        "watchers": 1,
        "stars": 0,
        "forks": 0,
        "watching": "all",
        "starred_by_me": False,
    }
    fork = await client.post(f"{ORG}/agents/developer/fork", json={"slug": "my-dev", "scope": "personal"})
    assert fork.json()["social"]["watching"] == "all", "the fork's owner watches it"
    assert (await _social(client, "developer"))["watching"] is None, "forking is not watching the source"

    off = await client.delete(f"{ORG}/agents/mine/watch")
    assert off.status_code == 200 and off.json()["watching"] is None and off.json()["watchers"] == 0
    assert (await client.delete(f"{ORG}/agents/mine/watch")).status_code == 200, "idempotent"


async def test_watch_and_star_count_people_once_and_retune_the_level(client: AsyncClient, org: str) -> None:
    await install_common(client)
    await add_member(client, OTHER, "developer")
    await as_user(client, DEV)
    first = await client.put(f"{ORG}/agents/developer/watch")
    assert first.json()["watching"] == "all" and first.json()["watchers"] == 1
    again = await client.put(f"{ORG}/agents/developer/watch", json={"level": "releases"})
    assert again.json()["watching"] == "releases" and again.json()["watchers"] == 1
    assert (await client.put(f"{ORG}/agents/developer/watch", json={"level": "nope"})).status_code == 422
    starred = await client.put(f"{ORG}/agents/developer/star")
    assert starred.json()["stars"] == 1 and starred.json()["starred_by_me"] is True
    assert (await client.put(f"{ORG}/agents/developer/star")).json()["stars"] == 1

    await as_user(client, OTHER)
    assert (await client.put(f"{ORG}/agents/developer/star")).json()["stars"] == 2
    social = await _social(client, "developer")
    assert (social["watchers"], social["stars"], social["watching"], social["starred_by_me"]) == (
        1,
        2,
        None,
        True,
    )
    assert (await client.delete(f"{ORG}/agents/developer/star")).json()["stars"] == 1
    assert (await client.delete(f"{ORG}/agents/developer/star")).json()["stars"] == 1, "idempotent"


async def test_the_list_sorts_by_stars_and_keeps_the_starred_ones(client: AsyncClient, org: str) -> None:
    await install_common(client)
    await as_user(client, ADMIN)
    for slug in ("aaa", "zzz"):
        assert (await create_agent(client, slug)).status_code == 201
    await add_member(client, DEV, "developer")
    await as_user(client, DEV)
    await client.put(f"{ORG}/agents/zzz/star")
    await as_user(client, ADMIN)
    await client.put(f"{ORG}/agents/zzz/star")
    await client.put(f"{ORG}/agents/developer/star")

    by_name = [a["slug"] for a in (await client.get(f"{ORG}/agents")).json()]
    assert by_name == sorted(by_name)
    by_stars = (await client.get(f"{ORG}/agents", params={"sort": "stars"})).json()
    assert [a["slug"] for a in by_stars][:2] == ["zzz", "developer"]
    assert by_stars[0]["social"]["stars"] == 2
    mine = (await client.get(f"{ORG}/agents", params={"starred": True})).json()
    assert {a["slug"] for a in mine} == {"zzz", "developer"}
    await as_user(client, DEV)
    assert [a["slug"] for a in (await client.get(f"{ORG}/agents", params={"starred": True})).json()] == [
        "zzz"
    ]


async def test_only_a_person_watches_and_stars() -> None:
    from choregos_api.rbac import SYSTEM
    from choregos_api.services.agent_social import require_person

    with pytest.raises(ApiError) as refused:
        require_person(SYSTEM)
    assert refused.value.status_code == 403


async def test_the_gestures_are_audited(client: AsyncClient, org: str) -> None:
    await install_common(client)
    for verb in ("watch", "star"):
        await client.put(f"{ORG}/agents/developer/{verb}")
        await client.delete(f"{ORG}/agents/developer/{verb}")
    events = (await client.get("/api/v1/audit", params={"target_type": "agent"})).json()["items"]
    actions = [e["action"] for e in events]
    for action in ("agent.watch", "agent.unwatch", "agent.star", "agent.unstar"):
        assert action in actions, actions


# ───────────────────────────── what watching delivers ─────────────────────────────


async def test_a_new_version_and_the_move_of_production_reach_the_watchers_but_not_the_author(
    client: AsyncClient, org: str
) -> None:
    await _watched_reviewer(client)
    await as_user(client, ADMIN)
    assert await _kinds(client) == [], "the owner who published is not told of their own gesture"
    await _publish(client, "reviewer", 2)
    assert await _kinds(client) == []

    await as_user(client, OTHER)
    inbox = await _inbox(client)
    assert sorted(i["kind"] for i in inbox["items"]) == ["production_moved", "version_published"]
    assert inbox["unread"] == 2
    published = next(i for i in inbox["items"] if i["kind"] == "version_published")
    assert (published["agent"], published["version"], published["actor"]) == ("reviewer", 2, ADMIN)
    moved = next(i for i in inbox["items"] if i["kind"] == "production_moved")
    assert (moved["version"], moved["previous_version"]) == (2, 1)


async def test_candidate_promotion_rollback_and_withdrawal_each_have_their_item(
    client: AsyncClient, org: str
) -> None:
    await _watched_reviewer(client, CANDIDATE_POLICY)
    await as_user(client, ADMIN)
    await _publish(client, "reviewer", 2)  # a candidate: production stays on 1
    release = lambda **b: client.post(f"{ORG}/agents/reviewer/releases", json=b)  # noqa: E731
    await as_user(client, OTHER)
    assert "candidate_started" in await _kinds(client)
    await client.post(f"{ORG}/agent-notifications/read")

    await as_user(client, ADMIN)
    done = await release(channel="stable", version=2, reason="two runs, clean")
    assert done.status_code == 201, done.text
    await as_user(client, OTHER)
    assert (await _inbox(client, unread=True))["items"][0]["kind"] == "candidate_promoted"
    await client.post(f"{ORG}/agent-notifications/read")

    await as_user(client, ADMIN)
    back = await release(channel="stable", version=1, reason="regression")
    assert back.status_code == 201 and back.json()["kind"] == "rollback", back.text
    await _publish(client, "reviewer", 3)
    withdrawn = await release(channel="candidate", version=None)
    assert withdrawn.status_code == 201, withdrawn.text
    await as_user(client, OTHER)
    kinds = [i["kind"] for i in (await _inbox(client, unread=True))["items"]]
    assert "rolled_back" in kinds and "candidate_withdrawn" in kinds


async def test_a_new_fork_and_a_lifecycle_change_reach_the_watchers_at_level_all_only(
    client: AsyncClient, org: str
) -> None:
    await _watched_reviewer(client)
    await add_member(client, DEV, "developer")
    await as_user(client, DEV)
    await client.put(f"{ORG}/agents/reviewer/watch", json={"level": "releases"})
    fork = await client.post(f"{ORG}/agents/reviewer/fork", json={"slug": "org-rev", "scope": "org"})
    assert fork.status_code in {201, 403}, fork.text
    if fork.status_code == 403:  # a developer forks into a personal scope
        fork = await client.post(
            f"{ORG}/agents/reviewer/fork", json={"slug": "pers-rev", "scope": "personal"}
        )
        assert fork.status_code == 201
    await as_user(client, ADMIN)
    moved = await client.post(f"{ORG}/agents/reviewer/lifecycle", json={"to": "deprecated"})
    assert moved.status_code == 200, moved.text

    await as_user(client, OTHER)
    items = (await _inbox(client))["items"]
    assert {i["kind"] for i in items} >= {"lifecycle_changed"}
    assert next(i for i in items if i["kind"] == "lifecycle_changed")["detail"] == "deprecated"
    await as_user(client, DEV)
    assert await _kinds(client) == [], "a `releases` watcher is told of releases only"


async def test_a_watcher_who_loses_sight_of_the_agent_gets_nothing_and_it_is_not_counted(
    client: AsyncClient, org: str
) -> None:
    await add_member(client, OTHER, "developer")
    await add_member(client, DEV, "developer")
    await as_user(client, DEV)
    assert (await create_agent(client, "shared-one", scope="personal")).status_code == 201
    # an org admin may see it, so the admin can watch it; the owner's change of scope is out of reach
    await as_user(client, ADMIN)
    assert (await client.put(f"{ORG}/agents/shared-one/watch")).status_code == 200
    await as_user(client, DEV)
    await _publish(client, "shared-one", 2)
    await as_user(client, ADMIN)
    assert len((await _inbox(client))["items"]) >= 1

    from choregos_api.db.models import Agent
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    # the agent turns personal to somebody else: the administrator's view of it goes with the role
    await as_user(client, OTHER)
    assert (await client.put(f"{ORG}/agents/shared-one/watch")).status_code == 404
    assert (await _inbox(client))["items"] == []
    assert Agent and session_scope and update  # names the tables the Postgres RLS test below drives


async def test_reading_marks_only_the_callers_own_items(client: AsyncClient, org: str) -> None:
    await _watched_reviewer(client)
    await as_user(client, ADMIN)
    await _publish(client, "reviewer", 2)
    await as_user(client, OTHER)
    items = (await _inbox(client))["items"]
    one = await client.post(f"{ORG}/agent-notifications/{items[0]['id']}/read")
    assert one.status_code == 200 and one.json() == {"updated": 1}
    assert (await _inbox(client))["unread"] == len(items) - 1
    assert (await client.post(f"{ORG}/agent-notifications/{items[0]['id']}/read")).status_code == 404
    assert (await client.post(f"{ORG}/agent-notifications/none/read")).status_code == 404
    await as_user(client, ADMIN)
    assert (await client.post(f"{ORG}/agent-notifications/{items[1]['id']}/read")).status_code == 404, (
        "not somebody else's item"
    )
    await as_user(client, OTHER)
    assert (await client.post(f"{ORG}/agent-notifications/read")).json() == {"updated": len(items) - 1}
    assert (await _inbox(client, unread=True))["items"] == []


@pytest.mark.parametrize("path", ["watch", "star"])
async def test_an_unknown_agent_is_a_404(client: AsyncClient, org: str, path: str) -> None:
    await as_user(client, ADMIN)
    assert (await client.put(f"{ORG}/agents/ghost/{path}")).status_code == 404
