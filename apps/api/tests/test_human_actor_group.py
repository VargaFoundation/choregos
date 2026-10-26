"""A human actor's `group` is enforced where a person answers (ADR 0044, ADR 0052): a project owner
outside `release-captains` cannot answer a step addressed to it; a member can; an organisation
administrator can, and the override is audited. A group nobody carries is warned about at validation.
"""

from __future__ import annotations

from typing import Any

from httpx import ASGITransport, AsyncClient

from .conftest import login

GROUP = "release-captains"
WORKFLOW = f"""
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: release
  version: 1
initial: waiting
actors:
  captain: {{type: human, group: {GROUP}}}
states:
  waiting: {{display: Waiting for the captain, kind: wait}}
  done: {{display: Done, terminal: true}}
transitions:
  - id: t-ship
    from: waiting
    to: done
    by: captain
"""


async def _member(client: AsyncClient, email: str, role: str, groups: list[str]) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    added = await client.post("/api/v1/orgs/varga/members", json={"email": email, "role": role})
    assert added.status_code in {200, 201}, added.text
    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.groups = groups


async def _session(app: Any, email: str) -> AsyncClient:
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(other, email)
    return other


async def _waiting_ticket(client: AsyncClient, pid: str) -> str:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    assert (
        await client.put(f"/api/v1/projects/{pid}/workflows/release", json={"yaml": WORKFLOW})
    ).status_code == 200
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "Ship 1.2", "workflow": "release"}
    )
    assert created.status_code == 201, created.text
    item_id = str(created.json()["id"])
    async with session_scope() as session:
        session.add(
            HumanRequest(
                work_item_id=item_id,
                project_id=pid,
                transition_id="t-ship",
                kind="approval",
                payload={"summary": "Ship?"},
                requested_at=utcnow(),
            )
        )
    return item_id


async def _decide(app: Any, email: str, item_id: str) -> Any:
    session = await _session(app, email)
    try:
        return await session.post(f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve"})
    finally:
        await session.aclose()


async def test_a_project_owner_outside_the_group_cannot_answer_its_step(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    item_id = await _waiting_ticket(client, project["id"])
    await _member(client, "owner@varga.dev", "project_owner", ["platform-team"])
    await _member(client, "dev@varga.dev", "developer", [])
    for outsider in ("owner@varga.dev", "dev@varga.dev"):
        refused = await _decide(app, outsider, item_id)
        assert refused.status_code == 403 and GROUP in refused.text, (outsider, refused.text)

    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(select(HumanRequest).where(HumanRequest.work_item_id == item_id))
        ).scalar_one()
        assert row.decided_at is None, "nothing was decided"


async def test_a_member_answers_and_an_administrator_overrides_with_an_audit_trail(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    item_id = await _waiting_ticket(client, project["id"])
    await _member(client, "cap@varga.dev", "developer", [GROUP])
    answered = await _decide(app, "cap@varga.dev", item_id)
    assert answered.status_code == 202, answered.text
    async with session_scope() as session:
        overrides = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "workitem.decision.group_override")
                )
            )
            .scalars()
            .all()
        )
    assert overrides == [], "a member of the group is not an override"

    second = await _waiting_ticket_again(client, project["id"])
    overridden = await client.post(f"/api/v1/work-items/{second}/decisions", json={"kind": "approve"})
    assert overridden.status_code == 202, overridden.text
    async with session_scope() as session:
        (entry,) = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "workitem.decision.group_override")
                )
            )
            .scalars()
            .all()
        )
    assert entry.payload["group"] == GROUP and entry.payload["transition"] == "t-ship"


async def _waiting_ticket_again(client: AsyncClient, pid: str) -> str:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    created = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "Ship 1.3", "workflow": "release"}
    )
    item_id = str(created.json()["id"])
    async with session_scope() as session:
        row = HumanRequest(work_item_id=item_id, project_id=pid, transition_id="t-ship", kind="approval",
                           payload={}, requested_at=utcnow())  # fmt: skip
        session.add(row)
    return item_id


async def test_a_group_nobody_carries_is_warned_about_at_validation(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    path = f"/api/v1/projects/{pid}/workflows/validate"
    unknown = (await client.post(path, json={"yaml": WORKFLOW})).json()
    assert unknown["valid"] is True
    assert [w["code"] for w in unknown["warnings"]] == ["actor.group_unknown"]
    assert (
        GROUP in unknown["warnings"][0]["message"]
        and unknown["warnings"][0]["path"] == "/actors/captain/group"
    )

    await _member(client, "cap@varga.dev", "developer", [GROUP])
    known = (await client.post(path, json={"yaml": WORKFLOW})).json()
    assert known["warnings"] == []

    blank = WORKFLOW.replace(f"group: {GROUP}", 'group: ""')
    codes = [w["code"] for w in (await client.post(path, json={"yaml": blank})).json()["warnings"]]
    assert "actor.group_empty" in codes
    stateless = (await client.post("/api/v1/workflows/validate", json={"yaml": blank})).json()
    assert "actor.group_empty" in [w["code"] for w in stateless["warnings"]]


# ── organisation-namespaced groups (S40-27) ──


async def test_a_group_namespaced_for_the_organisation_counts_as_its_bare_name(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.human_groups import groups_known_in
    from sqlalchemy import select

    path = f"/api/v1/projects/{project['id']}/workflows/validate"
    await _member(client, "other@varga.dev", "developer", [f"choregos:other-org:{GROUP}"])
    other = (await client.post(path, json={"yaml": WORKFLOW})).json()
    assert [w["code"] for w in other["warnings"]] == ["actor.group_unknown"], "another org's group"

    await _member(client, "cap@varga.dev", "developer", [f"choregos:varga:{GROUP}"])
    ours = (await client.post(path, json={"yaml": WORKFLOW})).json()
    assert ours["warnings"] == []
    async with session_scope() as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
        assert GROUP in await groups_known_in(session, org_id)


async def test_a_namespaced_group_answers_the_step_only_for_its_own_organisation(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    item_id = await _waiting_ticket(client, project["id"])
    await _member(client, "foreign@varga.dev", "developer", [f"choregos:other:{GROUP}"])
    refused = await _decide(app, "foreign@varga.dev", item_id)
    assert refused.status_code == 403, refused.text
    await _member(client, "cap@varga.dev", "developer", [f"choregos:varga:{GROUP}"])
    answered = await _decide(app, "cap@varga.dev", item_id)
    assert answered.status_code == 202, answered.text


def test_groups_for_org_keeps_bare_names_and_unwraps_only_its_own_prefix() -> None:
    from choregos_api.services.human_groups import groups_for_org

    got = groups_for_org(
        ["architects", "choregos:varga:pm", "choregos:other:ops", "choregos:varga:"], "varga"
    )
    assert {"architects", "pm"} <= got and "ops" not in got and "" not in got


def test_knowledge_group_scope_and_grants_read_namespaced_groups_for_their_org() -> None:
    from types import SimpleNamespace

    from choregos_api.knowledge import access
    from choregos_api.rbac import Principal, Role

    base = SimpleNamespace(scope="groups", groups=["architects"], restricted=False)
    grant = SimpleNamespace(subject_type="group", subject="architects", role="viewer")

    def person(group: str) -> Principal:
        return Principal(user_id="u", email="u@x.dev", org_roles={"varga": Role.DEVELOPER}, groups=[group])

    assert access._reaches(person("choregos:varga:architects"), "varga", base)
    assert not access._reaches(person("choregos:other:architects"), "varga", base)
    assert access._holds(person("choregos:varga:architects"), "varga", grant)
    assert not access._holds(person("choregos:other:architects"), "varga", grant)
