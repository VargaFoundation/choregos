"""What a run writes after reading a restricted knowledge base stays restricted (S42-11f, ADR 0114).

A work item marked with a restricted base is read, with everything under it, only by people who read
that base; a project member who does not still sees it listed (key, title, state), and nothing more.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login
from .test_knowledge_rights import ORG, _as, _uid

READER, STRANGER = "reader@varga.dev", "stranger@varga.dev"
MEMBER = {"projects": {"billing-api": "developer"}}


async def _marked(
    client: AsyncClient, project: dict[str, Any], marks: list[str] | None = None
) -> dict[str, str]:
    """`hr` restricted and granted to READER; a work item marked with it, a run and a pending question."""
    from choregos_api.db.models import HumanRequest, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    created = await client.post(ORG, json={"slug": "hr", "name": "HR", "restricted": True})
    assert created.status_code == 201, created.text
    reader = await _uid(client, READER, **MEMBER)
    await _uid(client, STRANGER, **MEMBER)
    await login(client, "admin@varga.dev")
    granted = await client.put(
        f"{ORG}/hr/grants", json={"grants": [{"subject_type": "user", "subject": reader, "role": "viewer"}]}
    )
    assert granted.status_code == 200, granted.text
    async with session_scope() as session:
        item = WorkItem(
            project_id=project["id"],
            tracker_key="BILLING-1",
            title="Salary bands",
            state="review",
            body_snapshot="the brief",
            restricted_knowledge=["hr"] if marks is None else marks,
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-hr",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="implement",
                status="succeeded",
            )
        )
        session.add(
            HumanRequest(
                work_item_id=item.id,
                project_id=project["id"],
                kind="approval",
                payload={"question": "Publish the bands of each grade?"},
                requested_at=utcnow(),
            )
        )
        return {"id": item.id, "run": "run-hr"}


def _reads(item: dict[str, str]) -> list[str]:
    return [
        f"/api/v1/work-items/{item['id']}",
        f"/api/v1/work-items/{item['id']}/timeline",
        f"/api/v1/work-items/{item['id']}/hand-offs",
        f"/api/v1/work-items/{item['id']}/artifacts",
        f"/api/v1/work-items/{item['id']}/journey",
        f"/api/v1/work-items/{item['id']}/runs",
        f"/api/v1/runs/{item['run']}",
        f"/api/v1/runs/{item['run']}/access",
    ]


async def test_a_member_who_does_not_read_the_base_reads_nothing_of_the_marked_work_item(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    item = await _marked(client, project)
    async with _as(client, STRANGER, **MEMBER) as stranger:
        for url in _reads(item):
            assert (await stranger.get(url)).status_code == 404, url
        decided = await stranger.post(f"/api/v1/work-items/{item['id']}/decisions", json={"kind": "approve"})
        assert decided.status_code == 404
    async with _as(client, READER, **MEMBER) as reader:
        for url in _reads(item):
            assert (await reader.get(url)).status_code == 200, url


async def test_the_list_shows_the_marked_work_item_without_its_content(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    item = await _marked(client, project)
    url = f"/api/v1/projects/{project['id']}/work-items"
    async with _as(client, STRANGER, **MEMBER) as stranger:
        (entry,) = (await stranger.get(url)).json()["items"]
    assert entry["redacted"] is True and entry["id"] == item["id"]
    assert (entry["tracker_key"], entry["title"], entry["state"]) == ("BILLING-1", "Salary bands", "review")
    assert entry["restricted_knowledge"] == ["hr"]
    assert entry["pending_request"] is None and entry["body_snapshot"] is None
    async with _as(client, READER, **MEMBER) as reader:
        (entry,) = (await reader.get(url)).json()["items"]
    assert entry["redacted"] is False
    assert entry["pending_request"]["payload"]["question"] == "Publish the bands of each grade?"


async def test_the_bases_are_read_as_they_are_now(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    item = await _marked(client, project, marks=["hr", "gone"])
    async with _as(client, READER, **MEMBER) as reader:
        assert (await reader.get(f"/api/v1/work-items/{item['id']}")).status_code == 404, (
            "a base that no longer exists keeps the work item closed"
        )
    await login(client, "admin@varga.dev")
    assert (await client.get(f"/api/v1/work-items/{item['id']}")).status_code == 200, (
        "an administrator reads it"
    )

    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        row = await session.get(WorkItem, item["id"])
        assert row is not None
        row.restricted_knowledge = ["hr"]
    assert (
        await client.patch(
            f"{ORG}/hr", json={"restricted": False, "scope": "projects", "projects": ["billing-api"]}
        )
    ).status_code == 200
    async with _as(client, STRANGER, **MEMBER) as stranger:
        assert (await stranger.get(f"/api/v1/work-items/{item['id']}")).status_code == 200, (
            "lifting the restriction opens the work items it marked"
        )


async def test_the_mcp_door_serves_nothing_of_it_to_a_member_who_does_not_read_the_base(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    from .test_mcp_porte import _jeton, _outil

    item = await _marked(client, project)
    async with _as(client, STRANGER, **MEMBER) as stranger:
        token = await _jeton(stranger, "mcp:read")
        stranger.cookies.clear()
        got = await _outil(stranger, token, "get_work_item", work_item="BILLING-1")
        assert got["result"]["isError"] is True
        run = await _outil(stranger, token, "summarize_run", run=item["run"])
        assert run["result"]["isError"] is True
        pending = (await _outil(stranger, token, "list_pending_decisions"))["result"]["structuredContent"]
        assert pending["pending"] == [], "the question is the run's content"
        found = await _outil(stranger, token, "search_work_items", project="billing-api")
        assert [(w["key"], w["restricted"]) for w in found["result"]["structuredContent"]["work_items"]] == [
            ("BILLING-1", True)
        ]
    async with _as(client, READER, **MEMBER) as reader:
        token = await _jeton(reader, "mcp:read")
        reader.cookies.clear()
        got = await _outil(reader, token, "get_work_item", work_item="BILLING-1")
        assert not got["result"].get("isError"), got


async def test_the_a2a_door_does_not_find_the_task_for_a_member_who_does_not_read_the_base(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    from .test_a2a_inbound import _rpc, _token

    item = await _marked(client, project)
    for who, found in ((STRANGER, False), (READER, True)):
        async with _as(client, who, **MEMBER) as person:
            token = await _token(person, "a2a")
            person.cookies.clear()
            answer = (await _rpc(person, token, "tasks/get", {"id": item["id"]})).json()
            assert ("result" in answer) is found, (who, answer)


@pytest.mark.parametrize("slugs", [[], ["hr"], ["hr", "legal"]])
def test_a_mark_is_only_ever_added_to(slugs: list[str]) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.knowledge.marked import mark

    item = WorkItem(restricted_knowledge=["legal"])
    assert mark(item, slugs) is (not set(slugs) <= {"legal"})
    assert item.restricted_knowledge == sorted({"legal", *slugs})


async def test_a_finding_of_a_marked_work_item_is_listed_only_to_its_readers(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Finding
    from choregos_api.db.session import session_scope

    item = await _marked(client, project)
    async with session_scope() as session:
        row = Finding(
            project_id=project["id"],
            origin_work_item_id=item["id"],
            title="Band E4 is underpaid",
            type="docs",
            severity="low",
            evidence="the grid",
            status="pending",
        )
        session.add(row)
        await session.flush()
        finding = row.id
    url = f"/api/v1/projects/{project['id']}/findings"
    async with _as(client, STRANGER, projects={"billing-api": "project_owner"}) as stranger:
        assert (await stranger.get(url)).json()["items"] == []
        acted = await stranger.post(f"/api/v1/findings/{finding}/actions", json={"action": "dismiss"})
        assert acted.status_code == 404
    async with _as(client, READER, **MEMBER) as reader:
        assert [f["id"] for f in (await reader.get(url)).json()["items"]] == [finding]


async def test_a_title_an_agent_wrote_is_not_listed(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    item = await _marked(client, project)
    async with session_scope() as session:
        row = await session.get(WorkItem, item["id"])
        assert row is not None
        row.initiated_by = "work_item:BILLING-0"
    async with _as(client, STRANGER, **MEMBER) as stranger:
        (entry,) = (await stranger.get(f"/api/v1/projects/{project['id']}/work-items")).json()["items"]
    assert entry["title"] == "Restricted work item"


async def test_an_external_tracker_receives_a_neutral_issue_for_a_marked_child(
    client: AsyncClient, admin: str, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_adapters.fakes.tracker import FakeTracker
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.marked import WITHHELD
    from choregos_api.rbac import SYSTEM
    from choregos_api.schemas import WorkItemCreate
    from choregos_api.services import tickets

    fake = FakeTracker("billing-api")
    real = tickets.tracker_du_projet

    async def with_the_fake(session: Any, owner: Any) -> Any:
        row, _ = await real(session, owner)
        return row, fake

    monkeypatch.setattr(tickets, "tracker_du_projet", with_the_fake)
    connected = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={"type": "github-issues", "config": {"repo": "varga/billing-api"}},
    )
    assert connected.status_code == 200, connected.text
    async with session_scope() as session:
        owner = await session.get(Project, project["id"])
        child = await tickets.creer_un_ticket(
            session,
            SYSTEM,
            owner,
            WorkItemCreate(title="Band E4 for Ana", body="91k", start=False),
            canal="fan-out",
            initiated_by="work_item:BILLING-1",
            restricted_knowledge=["hr"],
        )
        assert (child.title, child.restricted_knowledge) == ("Band E4 for Ana", ["hr"])
    (issue,) = fake.items.values()
    assert issue.title == "Restricted work item" and WITHHELD in (issue.body or "")
    assert "Ana" not in (issue.body or "")
