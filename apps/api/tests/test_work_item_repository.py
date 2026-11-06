"""A project's repositories and a work item's target, on the API (S58-03, ADR 0124).

The project of the scene holds `billing-api` (its primary) and, once declared, `diamctl`. A caller who
names a repository is answered at once: an unknown name is 422, a change once the work item is fixed to
its repository is 409; a tracker label is copied; the work item says which repository it works on.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

DIAMCTL = {"name": "diamctl", "url": "https://github.com/varga/diamctl.git", "test_command": "go test ./..."}
STRICT = """apiVersion: choregos/v1
kind: Workflow
metadata:
  name: fix
  version: 1
  inputs:
    type: object
    additionalProperties: false
    properties:
      finding: {type: string}
actors:
  dev: {type: human, group: dev}
states:
  todo: {display: To do, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-fix, from: todo, to: done, by: dev}
"""


async def _two_repositories(client: AsyncClient, project: dict[str, Any]) -> str:
    pid = project["id"]
    config = {**project["config"], "repositories": [DIAMCTL]}
    saved = await client.patch(f"/api/v1/projects/{pid}", json={"config": config})
    assert saved.status_code == 200, saved.text
    assert saved.json()["config"]["repositories"][0]["name"] == "diamctl"
    workflow = await client.put(f"/api/v1/projects/{pid}/workflows/fix", json={"yaml": STRICT})
    assert workflow.status_code == 200, workflow.text
    return str(pid)


async def _file(client: AsyncClient, pid: str, **body: Any) -> Any:
    return await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "CVE-2026-1 in a dependency", "start": False, "workflow": "fix", **body},
    )


async def _documents(item_id: str, **documents: Any) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, item_id)
        assert item is not None
        item.documents = {**(item.documents or {}), **documents}


async def test_the_project_config_takes_its_repositories_and_refuses_them_without_a_primary(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _two_repositories(client, project)
    pid = project["id"]
    orphan = {k: v for k, v in project["config"].items() if k != "repo"} | {"repositories": [DIAMCTL]}
    refused = await client.patch(f"/api/v1/projects/{pid}", json={"config": orphan})
    assert refused.status_code == 422, refused.text
    assert "without `repo`" in refused.text
    twice = {**project["config"], "repositories": [DIAMCTL | {"name": "billing-api"}]}
    assert (await client.patch(f"/api/v1/projects/{pid}", json={"config": twice})).status_code == 422


async def test_a_work_item_names_its_repository_or_works_on_the_primary(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = await _two_repositories(client, project)
    named = await _file(client, pid, fields={"finding": "CVE-2026-1", "repository": "diamctl"})
    assert named.status_code == 201, named.text
    assert named.json()["repository"] == "diamctl", "a strict workflow still takes the platform's field"
    assert named.json()["repository_fixed"] is False
    plain = await _file(client, pid, fields={"finding": "CVE-2026-2"})
    assert plain.json()["repository"] == "billing-api", "the primary, by its name"
    labelled = await _file(client, pid, fields={}, labels=["repository:diamctl", "security"])
    assert labelled.json()["fields"] == {"repository": "diamctl"}, "a tracker's label names it"


async def test_an_unknown_repository_is_refused_at_birth(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = await _two_repositories(client, project)
    refused = await _file(client, pid, fields={"repository": "diamclt"})
    assert refused.status_code == 422, refused.text
    assert "`diamclt` is not a repository" in refused.text
    assert "`billing-api`, `diamctl`" in refused.text
    assert refused.json()["errors"][0]["loc"] == ["fields", "repository"]


async def test_a_person_moves_a_work_item_until_it_is_fixed(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = await _two_repositories(client, project)
    item = (await _file(client, pid, fields={"finding": "CVE-2026-1"})).json()
    url = f"/api/v1/work-items/{item['id']}"

    unknown = await client.patch(url, json={"fields": {"repository": "diamclt"}})
    assert unknown.status_code == 422, unknown.text
    moved = await client.patch(url, json={"fields": {"repository": "diamctl"}})
    assert moved.status_code == 200, moved.text
    assert moved.json()["repository"] == "diamctl"

    await _documents(item["id"], repository_fixed="diamctl")  # a stage cloned it
    fixed = (await client.get(url)).json()
    assert fixed["repository"] == "diamctl" and fixed["repository_fixed"] is True
    refused = await client.patch(url, json={"fields": {"repository": "billing-api"}})
    assert refused.status_code == 409, refused.text
    assert "works on `diamctl` since its first step" in refused.text
    back_to_primary = await client.patch(url, json={"fields": {"repository": None}})
    assert back_to_primary.status_code == 409, "removing it would move it to the primary"
    same = await client.patch(url, json={"fields": {"repository": "diamctl", "finding": "CVE-2026-9"}})
    assert same.status_code == 200, same.text


async def test_a_work_item_that_ran_on_a_repository_is_fixed_to_it(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A run that cloned before the project held a second repository: no record, still fixed."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    pid = await _two_repositories(client, project)
    item = (await _file(client, pid, fields={})).json()
    async with session_scope(orgs="*") as session:
        session.add(
            Run(
                work_item_id=item["id"],
                project_id=pid,
                stage_role="implement",
                attempt=1,
                status="succeeded",
                stage_input={"repo": {"url": "https://github.com/varga/billing-api.git"}},
            )
        )
    url = f"/api/v1/work-items/{item['id']}"
    assert (await client.get(url)).json()["repository_fixed"] is True
    refused = await client.patch(url, json={"fields": {"repository": "diamctl"}})
    assert refused.status_code == 409, refused.text


async def test_a_project_with_one_repository_says_it_and_refuses_another_name(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    item = await client.post(f"/api/v1/projects/{pid}/work-items", json={"title": "A fix", "start": False})
    assert item.status_code == 201, item.text
    assert item.json()["repository"] == "billing-api"
    refused = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "A fix", "start": False, "fields": {"repository": "x"}},
    )
    assert refused.status_code == 422, refused.text


async def test_a_task_that_names_the_repository_is_held_to_the_same_rules(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A person's task values pour into the fields (S20-06): `repository` among them is checked too."""
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    pid = await _two_repositories(client, project)
    item = (await _file(client, pid, fields={"finding": "CVE-2026-1"})).json()
    form = {"type": "object", "required": ["repository"], "properties": {"repository": {"type": "string"}}}
    async with session_scope() as session:
        session.add(
            HumanRequest(
                work_item_id=item["id"],
                project_id=pid,
                transition_id="t-fix",
                kind="task",
                payload={"summary": "Where is the finding?", "form": form},
                requested_at=utcnow(),
            )
        )
    decisions = f"/api/v1/work-items/{item['id']}/decisions"
    refused = await client.post(decisions, json={"kind": "complete", "values": {"repository": "diamclt"}})
    assert refused.status_code == 422, refused.text
    assert "`diamclt` is not a repository" in refused.text
    done = await client.post(decisions, json={"kind": "complete", "values": {"repository": "diamctl"}})
    assert done.status_code == 202, done.text
    assert (await client.get(f"/api/v1/work-items/{item['id']}")).json()["repository"] == "diamctl"
