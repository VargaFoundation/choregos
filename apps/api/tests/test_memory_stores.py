"""Named memories, global and project (S40-11a, ADR 0075): rights, visibility, listing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from choregos_api.rbac import Principal, Role
from choregos_api.services.memory_access import reaches_project, visible_to
from httpx import AsyncClient

from .agent_scopes_kit import ORG, add_member, as_user, create_agent, make_project

MEM = f"{ORG}/memories"


async def _two_projects(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    billing = await make_project(client, "billing-api")
    checkout = await make_project(client, "checkout-web")
    await add_member(client, "owner@varga.dev", "project_owner", "billing-api")
    await add_member(client, "dev@varga.dev", "developer", "billing-api")
    await add_member(client, "other@varga.dev", "project_owner", "checkout-web")
    return billing, checkout


async def test_an_org_admin_creates_a_global_memory_and_a_developer_cannot(
    client: AsyncClient, admin: str
) -> None:
    created = await client.post(
        MEM, json={"slug": "security-lessons", "display_name": "Security lessons", "reach": "org"}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["scope"], body["reach"], body["status"], body["implicit"]) == (
        "global",
        "org",
        "active",
        False,
    )
    assert (
        await client.post(MEM, json={"slug": "security-lessons", "display_name": "Again"})
    ).status_code == 409
    await add_member(client, "dev@varga.dev", "developer")
    await as_user(client, "dev@varga.dev")
    refused = await client.post(MEM, json={"slug": "other-one", "display_name": "x"})
    assert refused.status_code == 403, refused.text
    assert (await client.patch(f"{MEM}/security-lessons", json={"display_name": "x"})).status_code == 403
    # ... but a member reads it.
    assert (await client.get(f"{MEM}/security-lessons")).status_code == 200


async def test_reserved_slugs_and_reach_rules(client: AsyncClient, admin: str) -> None:
    for slug in ("shared", "own"):
        refused = await client.post(MEM, json={"slug": slug, "display_name": "x"})
        assert refused.status_code == 422, (slug, refused.text)
    no_projects = await client.post(
        MEM, json={"slug": "scoped-one", "display_name": "x", "reach": "projects"}
    )
    assert no_projects.status_code == 422
    unknown = await client.post(
        MEM, json={"slug": "scoped-one", "display_name": "x", "reach": "projects", "projects": ["nope-nope"]}
    )
    assert unknown.status_code == 422 and "nope-nope" in unknown.text


async def test_a_project_owner_creates_a_project_memory_and_others_cannot(
    client: AsyncClient, admin: str
) -> None:
    billing, _ = await _two_projects(client)
    url = f"/api/v1/projects/{billing['id']}/memories"
    await as_user(client, "owner@varga.dev")
    created = await client.post(url, json={"slug": "decisions", "display_name": "Decisions"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["scope"], body["project"], body["reach"]) == ("project", "billing-api", None)
    assert (await client.post(url, json={"slug": "decisions", "display_name": "Again"})).status_code == 409
    assert (await client.post(url, json={"slug": "shared", "display_name": "x"})).status_code == 422
    # A project memory has no reach to change.
    assert (await client.patch(f"{url}/decisions", json={"reach": "org"})).status_code == 422
    renamed = await client.patch(f"{url}/decisions", json={"display_name": "Billing decisions"})
    assert renamed.status_code == 200 and renamed.json()["display_name"] == "Billing decisions"
    await as_user(client, "dev@varga.dev")
    assert (await client.post(url, json={"slug": "more-one", "display_name": "x"})).status_code == 403
    await as_user(client, "other@varga.dev")
    assert (await client.post(url, json={"slug": "more-one", "display_name": "x"})).status_code == 403
    assert (await client.patch(f"{url}/decisions", json={"status": "archived"})).status_code == 403


async def test_archive_a_global_and_a_project_memory(client: AsyncClient, admin: str) -> None:
    billing, _ = await _two_projects(client)
    await client.post(MEM, json={"slug": "lessons-one", "display_name": "L"})
    archived = await client.patch(f"{MEM}/lessons-one", json={"status": "archived"})
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    url = f"/api/v1/projects/{billing['id']}/memories"
    await client.post(url, json={"slug": "decisions", "display_name": "D"})
    done = await client.patch(f"{url}/decisions", json={"status": "archived"})
    assert done.json()["status"] == "archived"


async def test_list_project_memories_has_implicit_named_and_only_the_global_ones_reaching_it(
    client: AsyncClient, admin: str
) -> None:
    billing, _ = await _two_projects(client)
    await client.post(MEM, json={"slug": "everywhere", "display_name": "E", "reach": "org"})
    await client.post(
        MEM,
        json={"slug": "billing-only", "display_name": "B", "reach": "projects", "projects": ["billing-api"]},
    )
    await client.post(
        MEM,
        json={
            "slug": "checkout-only",
            "display_name": "C",
            "reach": "projects",
            "projects": ["checkout-web"],
        },
    )
    await client.post(
        f"/api/v1/projects/{billing['id']}/memories", json={"slug": "decisions", "display_name": "D"}
    )
    listed = (await client.get(f"/api/v1/projects/{billing['id']}/memories")).json()
    by_slug = {(m["scope"], m["slug"]): m for m in listed}
    assert set(by_slug) == {
        ("project", "shared"),
        ("project", "own"),
        ("project", "decisions"),
        ("global", "everywhere"),
        ("global", "billing-only"),
    }
    assert by_slug[("project", "shared")]["implicit"] and by_slug[("project", "own")]["implicit"]
    assert not by_slug[("project", "decisions")]["implicit"]
    # The org-wide listing carries the project memory too, filterable.
    org_wide = (await client.get(MEM, params={"scope": "project"})).json()
    assert [(m["slug"], m["project"]) for m in org_wide] == [("decisions", "billing-api")]
    reaching = {m["slug"] for m in (await client.get(MEM, params={"project": "checkout-web"})).json()}
    assert reaching == {"checkout-only"}


async def test_used_by_lists_the_agents_whose_latest_version_binds_the_memory(
    client: AsyncClient, admin: str
) -> None:
    await client.post(MEM, json={"slug": "lessons-one", "display_name": "L"})
    bound = await create_agent(
        client, "keeper", spec={"memories": [{"scope": "global", "slug": "lessons-one", "mode": "read"}]}
    )
    assert bound.status_code == 201, bound.text
    await create_agent(client, "plain")
    used = (await client.get(f"{MEM}/lessons-one")).json()["used_by"]
    assert used == [{"agent": "keeper", "version": 1}]
    # A newer version that no longer binds it takes the agent out.
    newer = await client.post(
        f"{ORG}/agents/keeper/versions", json={"instructions": "Do the thing, then say so."}
    )
    assert newer.status_code == 201, newer.text
    assert (await client.get(f"{MEM}/lessons-one")).json()["used_by"] == []


def _principal(org_role: Role | None = None, **projects: Role) -> Principal:
    return Principal(
        user_id="u",
        email="u@x",
        org_roles={"varga": org_role} if org_role else {},
        project_roles={f"varga/{slug}": role for slug, role in projects.items()},
    )


def _store(scope: str, reach: str | None, projects: list[str], status: str = "active") -> Any:
    return SimpleNamespace(scope=scope, reach=reach, projects=projects, status=status)


def test_visibility_follows_the_reach() -> None:
    org_wide = _store("global", "org", [])
    scoped = _store("global", "projects", ["billing-api"])
    mine = _store("project", None, ["billing-api"])
    admin = _principal(Role.ORG_ADMIN)
    billing_dev = _principal(None, **{"billing-api": Role.DEVELOPER})
    checkout_dev = _principal(None, **{"checkout-web": Role.DEVELOPER})
    nobody = _principal()
    assert all(visible_to(admin, "varga", s) for s in (org_wide, scoped, mine))
    assert visible_to(billing_dev, "varga", scoped) and visible_to(billing_dev, "varga", mine)
    assert not visible_to(checkout_dev, "varga", scoped), "another project's member does not see it"
    assert not visible_to(checkout_dev, "varga", mine)
    assert visible_to(checkout_dev, "varga", org_wide)
    assert not any(visible_to(nobody, "varga", s) for s in (org_wide, scoped, mine))


def test_which_runs_reach_which_memory() -> None:
    assert reaches_project(_store("global", "org", []), "any-project")
    assert reaches_project(_store("global", "projects", ["billing-api"]), "billing-api")
    assert not reaches_project(_store("global", "projects", ["billing-api"]), "checkout-web")
    assert reaches_project(_store("project", None, ["billing-api"]), "billing-api")
    assert not reaches_project(_store("project", None, ["billing-api"]), "checkout-web")
    assert not reaches_project(_store("global", "org", [], status="archived"), "billing-api")
