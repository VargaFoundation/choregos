"""`work_item.file` (S84-02, ADR 0197): a governed action files a work item in another project of the
organisation, through that project's tracker, as a person's request is filed there.

- The target project's tracker receives it (an external one first, as for a person's request), and the
  effect answers the key and the URL a later effect cites.
- The target project must accept the source (`accepts_items_from`); the same project, an unknown one,
  an action nobody approved: refused, nothing filed.
- Played again under the same key, it files nothing twice.
- Its policy is `approval`: a workflow's action that files waits for a person.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_adapters.fakes.tracker import FakeTracker

ORG = "/api/v1/orgs/northwind"


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> FakeTracker:
    """The external tracker of every project whose tracker is not internal: the test's fake."""
    from tranibor_api.services import tickets

    fake = FakeTracker("solution")
    real = tickets.project_tracker

    async def with_the_fake(session: Any, project: Any) -> Any:
        row, _ = await real(session, project)
        return row, (fake if row is not None and row.type not in {"internal", "fake"} else None)

    monkeypatch.setattr(tickets, "project_tracker", with_the_fake)
    return fake


async def _target(client: AsyncClient, slug: str, accepts: list[str], tracker: str | None) -> dict[str, Any]:
    config = {"slug": slug, "org": "northwind", "accepts_items_from": accepts}
    created = await client.post(f"{ORG}/projects", json={"slug": slug, "name": slug, "config": config})
    assert created.status_code == 201, created.text
    if tracker is not None:
        put = await client.put(
            f"/api/v1/projects/{created.json()['id']}/connectors/tracker",
            json={"type": tracker, "config": {"repo": f"northwind/{slug}"}},
        )
        assert put.status_code == 200, put.text
    return dict(created.json())


async def _approved_action(project_id: str, status: str = "approved") -> str:
    from tranibor_api.db.models import Action, WorkItem
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        item = WorkItem(project_id=project_id, tracker_key="DESK-7", title="the export fails", state="x")
        session.add(item)
        await session.flush()
        action = Action(project_id=project_id, origin="transition", kind="bug.file", title="File the bug",
                        params={}, effects=[], proposed_by={}, approval={}, decisions=[], status=status,
                        work_item_id=item.id)  # fmt: skip
        from tranibor_api.db.models import Project

        project = await session.get(Project, project_id)
        assert project is not None
        action.org_id = project.org_id
        session.add(action)
        await session.flush()
        return str(action.id)


async def _play(project_id: str, action_id: str, params: dict[str, Any], key: str = "a:0") -> dict[str, Any]:
    from tranibor_api.db.models import Action, Project
    from tranibor_api.db.session import session_scope
    from tranibor_api.effects import EffectContext, effect

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        action = await session.get(Action, action_id)
        assert project is not None and action is not None
        return await effect("work_item.file")(EffectContext(session, action, project, key=key), params)


BUG = {
    "project": "solution",
    "title": "Expense export fails with a 500",
    "body": "Steps: …",
    "labels": ["bug"],
}


async def test_the_target_projects_tracker_receives_it_and_a_replay_files_nothing_twice(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker
) -> None:
    from tranibor_api.effects import effect_policy

    assert effect_policy("work_item.file") == "approval", "filing elsewhere waits for a person"
    await _target(client, "solution", [project["slug"]], "github-issues")
    action = await _approved_action(project["id"])

    filed = await _play(project["id"], action, BUG)
    assert filed["project"] == "solution" and filed["key"]
    created = [call for call in tracker.calls if call[0] == "create_item"]
    assert len(created) == 1
    (item,) = [i for i in tracker.items.values() if i.title == BUG["title"]]
    assert "bug" in item.labels and f"from:{project['slug']}" in item.labels
    assert "DESK-7" in item.body, "the issue says which work item filed it"

    again = await _play(project["id"], action, BUG)
    assert again == filed, "the same key finds the work item it filed"
    assert len([call for call in tracker.calls if call[0] == "create_item"]) == 1


async def test_an_internal_target_mints_its_own_key(client: AsyncClient, project: dict[str, Any]) -> None:
    target = await _target(client, "solution", [project["slug"]], None)
    filed = await _play(project["id"], await _approved_action(project["id"]), BUG)
    listed = (await client.get(f"/api/v1/projects/{target['id']}/work-items")).json()
    items = listed if isinstance(listed, list) else listed["items"]
    (item,) = [i for i in items if i["id"] == filed["work_item_id"]]
    assert item["title"] == BUG["title"] and item["tracker_key"] == filed["key"]


@pytest.mark.parametrize(
    ("accepts", "params", "status", "refusal"),
    [
        ([], BUG, "approved", "does not accept"),
        (["billing-api"], {**BUG, "project": "nowhere"}, "approved", "no other project"),
        (["billing-api"], {**BUG, "project": "billing-api"}, "approved", "no other project"),
        (["billing-api"], {**BUG, "title": " "}, "approved", "needs a `project` and a `title`"),
        (["billing-api"], BUG, "pending_approval", "not approved"),
    ],
    ids=["not-accepted", "unknown-project", "same-project", "no-title", "not-approved"],
)
async def test_what_is_not_opened_to_it_files_nothing(
    client: AsyncClient,
    project: dict[str, Any],
    tracker: FakeTracker,
    accepts: list[str],
    params: dict[str, Any],
    status: str,
    refusal: str,
) -> None:
    from tranibor_api.effects import EffectRefused

    await _target(client, "solution", accepts, "github-issues")
    action = await _approved_action(project["id"], status)
    with pytest.raises(EffectRefused, match=refusal):
        await _play(project["id"], action, params)
    assert not [call for call in tracker.calls if call[0] == "create_item"]


async def test_a_project_config_refuses_a_slug_that_is_not_one(client: AsyncClient, admin: str) -> None:
    config = {"slug": "solution", "org": "northwind", "accepts_items_from": ["Not A Slug"]}
    created = await client.post(f"{ORG}/projects", json={"slug": "solution", "name": "s", "config": config})
    assert created.status_code == 422
