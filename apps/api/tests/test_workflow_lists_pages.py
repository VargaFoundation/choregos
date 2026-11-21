"""The project's workflow, agent and application lists are paged in SQL (ADR 0206).

`listWorkflows`, `listWorkflowVersions`, `listProjectAgents`, `listImplicitAgents`,
`listWorkflowAgents` and `listMachineAccounts` used to answer a bare array: the whole table, or the
first rows of it. Each now answers `{items, meta}`, a page at a time by keyset: every row is reached
once, in the order the route always had, whatever the page size.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_core.domain import utcnow


def _base(project: dict[str, Any]) -> str:
    return f"/api/v1/projects/{project['id']}"


async def _pages(client: AsyncClient, url: str, key: Any, **params: Any) -> list[Any]:
    """Every row of a list, page after page, by the cursor each page hands back."""
    seen: list[Any] = []
    cursor: str | None = None
    for _ in range(100):
        query = {**params, **({"cursor": cursor} if cursor else {})}
        response = await client.get(url, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"items", "meta"}
        seen += [key(item) for item in body["items"]]
        if not body["meta"]["has_more"]:
            assert body["meta"]["next_cursor"] is None
            return seen
        cursor = body["meta"]["next_cursor"]
        assert cursor
    raise AssertionError("the list never ended")


async def _workflows(project: dict[str, Any], names: list[str], *, same_instant: bool = True) -> None:
    from tranibor_api.db.models import WorkflowDef
    from tranibor_api.db.session import session_scope

    start = utcnow()
    async with session_scope() as session:
        for index, name in enumerate(names):
            session.add(
                WorkflowDef(
                    project_id=project["id"],
                    name=name,
                    version=1,
                    yaml="",
                    json_doc={"actors": {}},
                    checksum=f"c-{index}",
                    is_active=True,
                    created_at=start if same_instant else start + timedelta(seconds=index),
                )
            )


@pytest.mark.parametrize("same_instant", [False, True])
async def test_every_workflow_is_reachable_once_by_name(
    client: AsyncClient, project: dict[str, Any], same_instant: bool
) -> None:
    # 130 workflows: past the 50 of a default page, and, all published in one instant, ordered by name
    # then by id, so no page break skips or repeats one.
    names = [f"flow-{index:03d}" for index in range(130)]
    await _workflows(project, names, same_instant=same_instant)
    first = (await client.get(f"{_base(project)}/workflows")).json()
    assert len(first["items"]) == 50 and first["meta"]["has_more"] is True
    seen = await _pages(client, f"{_base(project)}/workflows", lambda w: w["name"], limit=50)
    assert len(seen) == len(set(seen)), "each once"
    flows = [name for name in seen if name.startswith("flow-")]
    assert flows == names, "by name, each once"
    small = await _pages(client, f"{_base(project)}/workflows", lambda w: w["name"], limit=7)
    assert small == seen


async def test_the_open_items_are_counted_for_the_pages_workflows(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from tranibor_api.db.models import WorkflowDef, WorkItem
    from tranibor_api.db.session import session_scope

    await _workflows(project, ["zz-one", "zz-two", "zz-three"])
    async with session_scope() as session:
        from sqlalchemy import select

        wanted = (
            await session.execute(
                select(WorkflowDef).where(
                    WorkflowDef.project_id == project["id"], WorkflowDef.name == "zz-two"
                )
            )
        ).scalar_one()
        for number in range(3):
            session.add(
                WorkItem(
                    project_id=project["id"],
                    workflow_def_id=wanted.id,
                    tracker_key=f"BILLING-{900 + number}",
                    title="T",
                    state="ready",
                )
            )
    rows = await _pages(
        client, f"{_base(project)}/workflows", lambda w: (w["name"], w["open_items"]), limit=1
    )
    assert ("zz-two", 3) in rows and ("zz-one", 0) in rows and ("zz-three", 0) in rows


async def test_the_versions_come_newest_first_each_once(client: AsyncClient, project: dict[str, Any]) -> None:
    from tranibor_api.db.models import WorkflowDef
    from tranibor_api.db.session import session_scope

    start = utcnow()
    async with session_scope() as session:
        for version in range(1, 121):
            session.add(
                WorkflowDef(
                    project_id=project["id"],
                    name="history",
                    version=version,
                    yaml="",
                    json_doc={},
                    checksum=f"h-{version}",
                    is_active=version == 120,
                    created_at=start,  # all in one instant: the version alone orders them
                )
            )
    url = f"{_base(project)}/workflows/history/versions"
    seen = await _pages(client, url, lambda v: v["version"], limit=25)
    assert seen == list(range(120, 0, -1))
    assert (await client.get(url, params={"limit": 201})).status_code == 422
    assert (await client.get(url, params={"cursor": "not-a-cursor"})).status_code == 422


async def test_the_pins_come_by_agent_slug_each_once(client: AsyncClient, project: dict[str, Any]) -> None:
    slugs = [f"agent-{index:03d}" for index in range(75)]
    for slug in slugs:
        created = await client.post(
            "/api/v1/orgs/northwind/agents",
            json={"slug": slug, "kind": "internal", "display_name": slug, "spec": {}},
        )
        assert created.status_code == 201, created.text
        pinned = await client.put(f"{_base(project)}/agents/{slug}", json={"version": 1})
        assert pinned.status_code == 200, pinned.text
    seen = await _pages(client, f"{_base(project)}/agents", lambda p: p["agent"], limit=20)
    assert seen == slugs
    assert len((await client.get(f"{_base(project)}/agents")).json()["items"]) == 50


async def test_the_actors_page_across_workflows_and_inside_one(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from tranibor_api.db.models import WorkflowDef
    from tranibor_api.db.session import session_scope

    created = await client.post(
        "/api/v1/orgs/northwind/agents",
        json={"slug": "named", "kind": "internal", "display_name": "n", "spec": {}},
    )
    assert created.status_code == 201, created.text
    expected: list[tuple[str, str]] = []
    async with session_scope() as session:
        # 60 workflows (more than the batch the route reads at a time): one fat, the rest with a pair.
        for index in range(60):
            name = f"wf-{index:03d}"
            actors: dict[str, Any] = {"human": {"type": "human"}}
            for actor in ("a", "b") if index else tuple(f"x{n:02d}" for n in range(30)):
                actors[actor] = {"type": "agent", "role": "implement"}
                expected.append((name, actor))
            if index % 2:
                actors["registered"] = {"type": "agent", "agent": "named@1"}
            session.add(
                WorkflowDef(
                    project_id=project["id"],
                    name=name,
                    version=1,
                    yaml="",
                    json_doc={"actors": actors},
                    checksum=f"a-{index}",
                    is_active=True,
                )
            )
    implicit = await _pages(
        client, f"{_base(project)}/implicit-agents", lambda a: (a["workflow"], a["actor"]), limit=9
    )
    # The project's own default workflows come first by name: the seeded ones are left out of the count.
    implicit = [pair for pair in implicit if pair[0].startswith("wf-")]
    assert implicit == sorted(expected), "each implicit actor once, the registered ones left out"
    every = await _pages(
        client, f"{_base(project)}/workflow-agents", lambda a: (a["workflow"], a["actor"]), limit=9
    )
    registered = [(f"wf-{index:03d}", "registered") for index in range(60) if index % 2]
    every = [pair for pair in every if pair[0].startswith("wf-")]
    assert every == sorted(expected + registered)
    # One large page equals the same rows read in small ones.
    whole = await _pages(
        client, f"{_base(project)}/workflow-agents", lambda a: (a["workflow"], a["actor"]), limit=200
    )
    assert [pair for pair in whole if pair[0].startswith("wf-")] == every


async def test_the_machine_accounts_come_by_slug_each_once(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    slugs = [f"app-{index:03d}" for index in range(60)]
    for slug in slugs:
        created = await client.post(f"{_base(project)}/machine-accounts", json={"slug": slug})
        assert created.status_code == 201, created.text
    assert (await client.delete(f"{_base(project)}/machine-accounts/app-005")).status_code == 204
    seen = await _pages(client, f"{_base(project)}/machine-accounts", lambda a: a["slug"], limit=25)
    assert seen == [slug for slug in slugs if slug != "app-005"], "a removed account is not listed"
    first = (await client.get(f"{_base(project)}/machine-accounts")).json()
    assert all(account["address"].endswith("@machines.choregos.invalid") for account in first["items"])
    assert all(account["tokens"] == [] for account in first["items"])
