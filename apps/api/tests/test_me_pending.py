"""What waits for me, across the organisation (S67-07, ADR 0154 §4, G8).

`GET /me/pending` lists four kinds, each from its own table and each under the rule of the route that
decides it: a human step the person may answer themselves (read right, `item:decide`, addressed to them or
their groups), a governed action they may approve (rank, group, separation of duties), and the writes and
launches an assistant proposed in their conversations. A person sees no step of a project they cannot read,
no step addressed to a group they are outside of, and no action they proposed themselves.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from choregos_core import utcnow
from httpx import AsyncClient
from sqlalchemy import select

from .agent_scopes_kit import make_project
from .kits.conversations import _open
from .kits.human_actor import _member, _session
from .kits.human_requests import _request
from .kits.memberships import _only
from .test_agent_sessions import _agent, upstream  # noqa: F401

PENDING = "/api/v1/me/pending"
LEA = "lea@northwind.dev"
MAX = "max@northwind.dev"
APPROVERS = "netops-approvers"


async def _action(
    project_id: str,
    title: str,
    *,
    proposed_by: str | None = None,
    group: str | None = None,
    role: str = "project_owner",
    age: timedelta = timedelta(0),
) -> str:
    """A governed action waiting for approval (the fake of this kind: a row, nothing reaches Temporal)."""
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        project = await session.get(Project, project_id)
        assert project is not None
        rule: dict[str, Any] = {"role": role, "min": 1}
        if group:
            rule["group"] = group
        row = Action(
            org_id=project.org_id,
            project_id=project_id,
            origin="tool",
            kind="net.firewall_rule",
            title=title,
            effects=[],
            proposed_by={"kind": "user", "id": proposed_by or "agent:x"}
            if proposed_by
            else {"kind": "agent", "id": "agent:nobody"},
            approval={"approvers": [rule], "separation_of_duties": True},
            status="pending_approval",
        )
        session.add(row)
        await session.flush()
        row.created_at = utcnow() - age
        return row.id


async def _proposal(conversation_id: str, user_email: str, summary: str, *, kind: str = "write") -> str:
    """A write or a launch an assistant proposed (the fake of this kind: a row, nothing reaches a source)."""
    from choregos_api.db.models import AgentSessionRecord, ConversationAction, User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation_id)
        user = (await session.execute(select(User).where(User.email == user_email))).scalar_one()
        assert record is not None
        row = ConversationAction(
            org_id=record.org_id,
            conversation_id=conversation_id,
            user_id=user.id,
            need="orders" if kind == "write" else "intake",
            operation="commander_poste" if kind == "write" else "start",
            arguments={},
            summary=summary,
            status="proposed",
            kind=kind,
            target={"project": "billing-api", "workflow": "intake"} if kind == "launch" else None,
        )
        session.add(row)
        await session.flush()
        return row.id


async def _pending(app: Any, email: str) -> list[dict[str, Any]]:
    person = await _session(app, email)
    try:
        response = await person.get(PENDING)
        assert response.status_code == 200, response.text
        return list(response.json()["items"])
    finally:
        await person.aclose()


def _ids(items: list[dict[str, Any]], kind: str) -> set[str]:
    return {i["id"] for i in items if i["kind"] == kind}


async def test_a_person_without_a_session_is_refused(client: AsyncClient, org: str) -> None:
    assert (await client.get(PENDING)).status_code == 401


async def test_a_human_step_is_listed_for_its_assignee_and_with_the_route_that_decides_it(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    await _member(client, LEA, "developer", [])
    await _member(client, MAX, "developer", [])
    item_id, key, request_id = await _request(client, project["id"], "Ship 1.2", user=LEA)
    _, _, others = await _request(client, project["id"], "Ship 2.0", user=MAX)

    mine = await _pending(app, LEA)
    assert _ids(mine, "human_step") == {request_id}, "a step addressed to someone else is not mine"
    (step,) = mine
    assert step["what"] == "Ship 1.2?" and step["where"]["work_item_key"] == key
    assert step["where"]["project_slug"] == "billing-api" and step["where"]["org"] == "northwind"
    assert step["decision"]["method"] == "POST"
    assert step["decision"]["path"] == f"/work-items/{item_id}/decisions"
    assert step["decision"]["url"].endswith(f"/p/billing-api/items/{item_id}#decision")
    assert others not in {i["id"] for i in mine}


async def test_a_step_addressed_to_a_group_is_not_listed_outside_it(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope

    await _member(client, LEA, "developer", ["finance"])
    await _member(client, MAX, "developer", [])
    _, _, request_id = await _request(client, project["id"], "Close the quarter", user=LEA)
    async with session_scope() as session:
        row = await session.get(HumanRequest, request_id)
        assert row is not None
        row.assignee = {"user": None, "group": "finance"}

    assert _ids(await _pending(app, LEA), "human_step") == {request_id}
    assert await _pending(app, MAX) == [], "outside the group, the step is not mine"


async def test_a_step_of_a_project_the_person_cannot_read_is_not_listed(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    other = await make_project(client, "payroll")
    _, _, visible = await _request(client, project["id"], "Billing step", user=LEA)
    await _request(client, other["id"], "Payroll step", user=LEA)
    person = await _session(app, LEA)  # a sign-in makes them a member; then only billing-api is theirs
    try:
        await _only(LEA, "developer", "billing-api")
        response = await person.get(PENDING)
    finally:
        await person.aclose()
    assert _ids(response.json()["items"], "human_step") == {visible}, "payroll stays out of sight"


async def test_a_governed_action_is_listed_to_whoever_may_approve_it_never_to_its_proposer(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    await _member(client, LEA, "project_owner", [APPROVERS])
    await _member(client, MAX, "project_owner", [])
    proposed_by_lea = await _action(project["id"], "Open port 443", proposed_by=LEA)
    by_agent = await _action(project["id"], "Open port 8443", group=APPROVERS)

    lea = await _pending(app, LEA)
    assert _ids(lea, "governed_action") == {by_agent}, "not the action I proposed myself"
    (action,) = lea
    assert action["what"] == "Open port 8443"
    assert action["decision"]["path"] == f"/projects/{project['id']}/actions/{by_agent}/decision"
    assert action["decision"]["url"].endswith(f"/p/billing-api/actions/{by_agent}")

    max_ = await _pending(app, MAX)
    assert _ids(max_, "governed_action") == {proposed_by_lea}, (
        "another owner decides Lea's; the group gates the other"
    )


async def test_a_governed_action_of_a_project_the_person_cannot_read_is_not_listed(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    other = await make_project(client, "payroll")
    await _action(project["id"], "Billing action")
    await _action(other["id"], "Payroll action")
    person = await _session(app, LEA)
    try:
        await _only(LEA, "project_owner", "billing-api")
        response = await person.get(PENDING)
    finally:
        await person.aclose()
    assert [i["what"] for i in response.json()["items"]] == ["Billing action"]


async def test_a_governed_action_needs_the_rank_of_its_rule(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    await _member(client, LEA, "developer", [])
    await _action(project["id"], "Release captain only", role="release_captain")
    assert await _pending(app, LEA) == [], "a developer is below the rule's rank"


async def test_the_proposals_of_my_conversations_are_mine_alone_and_a_launch_is_its_own_kind(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    app: Any,
    upstream: Any,  # noqa: F811
) -> None:
    await _agent(client)
    mine = await _open("northwind", admin)
    write = await _proposal(mine, admin, "Order two laptops")
    launch = await _proposal(mine, admin, "Start the intake of Acme", kind="launch")

    items = await _pending(app, admin)
    assert _ids(items, "conversation_action") == {write} and _ids(items, "launch") == {launch}
    start = next(i for i in items if i["kind"] == "launch")
    assert start["where"]["conversation_id"] == mine and start["where"]["project_slug"] == "billing-api"
    assert start["decision"]["path"] == f"/me/conversations/{mine}/actions/{launch}/confirm"
    assert start["decision"]["url"].endswith(f"/conversations/{mine}?action={launch}")

    await _member(client, LEA, "project_owner", [])
    assert await _pending(app, LEA) == [], "a proposal is its conversation's owner's alone"


async def test_the_list_is_newest_first_across_kinds_and_cut_at_the_limit(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    await _member(client, LEA, "project_owner", [])
    old = await _action(project["id"], "Old", age=timedelta(hours=3))
    _, _, step = await _request(client, project["id"], "Recent", user=LEA)
    middle = await _action(project["id"], "Middle", age=timedelta(hours=1))

    items = await _pending(app, LEA)
    assert [i["id"] for i in items] == [step, middle, old]
    person = await _session(app, LEA)
    try:
        cut = (await person.get(PENDING, params={"limit": 1})).json()["items"]
    finally:
        await person.aclose()
    assert [i["id"] for i in cut] == [step]
