"""An organisation's own groups (S66-40, ADR 0160): read by its members, written by its administrator,
`internal` or `scim` (read-only here), merged into what a person carries, so that a member of the
internal group `release-captains` answers a step addressed to it without the identity provider's claim.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .kits.human_actor import _decide, _member, _session

ORG = "varga"
BASE = f"/api/v1/orgs/{ORG}/groups"
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


async def _user_id(email: str) -> str:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return str((await session.execute(select(User.id).where(User.email == email))).scalar_one())


async def _org_id() -> str:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return str(
            (await session.execute(select(Organization.id).where(Organization.slug == ORG))).scalar_one()
        )


async def _audited(action: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()
        return [dict(row.payload or {}) for row in rows]


async def _create(client: AsyncClient, slug: str = GROUP, **extra: Any) -> Any:
    return await client.post(BASE, json={"slug": slug, "name": slug.replace("-", " ").title(), **extra})


async def _scim_group(members: list[str], *, slug: str = "platform-team", external_id: str = "ext-1") -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.org_groups import upsert_external_group

    org_id = await _org_id()
    async with session_scope() as session:
        await upsert_external_group(
            session,
            org_id,
            external_id=external_id,
            slug=slug,
            name=slug.title(),
            members_user_ids=members,
            actor="scim:test",
        )


# ── the groups, through the API ──


async def test_an_administrator_creates_reads_updates_and_deletes_an_internal_group(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    created = await _create(client, description="Who ships")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["slug"] == GROUP and body["source"] == "internal" and body["member_count"] == 0
    assert body["description"] == "Who ships" and body["created_by"] == "admin@varga.dev"

    again = await _create(client)
    assert again.status_code == 409 and again.json()["code"] == "group_exists", again.text
    assert (await client.post(BASE, json={"slug": "Bad_Slug", "name": "x"})).status_code == 422

    listed = (await client.get(BASE)).json()["items"]
    assert [(g["slug"], g["source"], g["member_count"]) for g in listed] == [(GROUP, "internal", 0)]

    patched = await client.patch(f"{BASE}/{GROUP}", json={"name": "Captains", "description": None})
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Captains" and patched.json()["description"] is None
    assert (await client.get(f"{BASE}/{GROUP}")).json()["name"] == "Captains"

    assert (await client.delete(f"{BASE}/{GROUP}")).status_code == 204
    assert (await client.get(f"{BASE}/{GROUP}")).status_code == 404
    assert [p["group"] for p in await _audited("group.create")] == [GROUP]
    assert [p["fields"] for p in await _audited("group.update")] == [["name", "description"]]
    assert [p["group"] for p in await _audited("group.delete")] == [GROUP]


async def test_a_developer_reads_the_groups_and_writes_none_of_them(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    assert (await _create(client)).status_code == 201
    await _member(client, "dev@varga.dev", "developer", [])
    dev_id = await _user_id("dev@varga.dev")
    developer = await _session(app, "dev@varga.dev")
    try:
        assert (await developer.get(BASE)).status_code == 200
        assert (await developer.get(f"{BASE}/{GROUP}/members")).status_code == 200
        writes = [
            await developer.post(BASE, json={"slug": "mine", "name": "Mine"}),
            await developer.patch(f"{BASE}/{GROUP}", json={"name": "Taken"}),
            await developer.delete(f"{BASE}/{GROUP}"),
            await developer.post(f"{BASE}/{GROUP}/members", json={"user_id": dev_id}),
            await developer.delete(f"{BASE}/{GROUP}/members/{dev_id}"),
        ]
        assert [w.status_code for w in writes] == [403] * 5, [w.text for w in writes]
    finally:
        await developer.aclose()


async def test_someone_outside_the_organisation_reads_nothing(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    assert (await _create(client)).status_code == 201
    from choregos_api.db.models import Membership
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete

    stranger = await _session(app, "stranger@elsewhere.dev")
    async with session_scope() as session:
        user_id = await _user_id("stranger@elsewhere.dev")
        await session.execute(delete(Membership).where(Membership.user_id == user_id))
    try:
        refused = await stranger.get(BASE)
        assert refused.status_code == 403 and "member" in refused.text, refused.text
    finally:
        await stranger.aclose()


async def test_only_a_member_of_the_organisation_joins_a_group(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    assert (await _create(client)).status_code == 201
    await _member(client, "dev@varga.dev", "developer", [])
    dev_id = await _user_id("dev@varga.dev")
    members = f"{BASE}/{GROUP}/members"

    # someone who exists in the instance but not in the organisation, and someone who does not exist
    await _session_user("outsider@elsewhere.dev")
    for body in ({"email": "outsider@elsewhere.dev"}, {"email": "nobody@varga.dev"}, {"user_id": "nope"}):
        refused = await client.post(members, json=body)
        assert refused.status_code == 422 and "not a member" in refused.text, refused.text
    both = await client.post(members, json={"email": "dev@varga.dev", "user_id": dev_id})
    assert both.status_code == 422

    added = await client.post(members, json={"email": "dev@varga.dev"})
    assert added.status_code == 201, added.text
    assert added.json()["user_id"] == dev_id and added.json()["added_by"] == "admin@varga.dev"
    assert (await client.post(members, json={"user_id": dev_id})).status_code == 201, (
        "adding twice changes nothing"
    )
    listed = (await client.get(members)).json()["items"]
    assert [m["email"] for m in listed] == ["dev@varga.dev"]
    assert (await client.get(f"{BASE}/{GROUP}")).json()["member_count"] == 1
    assert [p["user_id"] for p in await _audited("group.member.add")] == [dev_id]

    assert (await client.delete(f"{members}/{dev_id}")).status_code == 204
    assert (await client.delete(f"{members}/{dev_id}")).status_code == 404
    assert (await client.get(members)).json()["items"] == []
    assert [p["user_id"] for p in await _audited("group.member.remove")] == [dev_id]


async def test_a_group_the_identity_provider_pushes_is_read_only_here(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _member(client, "dev@varga.dev", "developer", [])
    dev_id = await _user_id("dev@varga.dev")
    await _scim_group([dev_id])
    one = f"{BASE}/platform-team"
    read = (await client.get(one)).json()
    assert read["source"] == "scim" and read["member_count"] == 1
    writes = [
        await client.patch(one, json={"name": "Renamed"}),
        await client.delete(one),
        await client.post(f"{one}/members", json={"email": "admin@varga.dev"}),
        await client.delete(f"{one}/members/{dev_id}"),
    ]
    for refused in writes:
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "group_read_only" and "identity provider" in refused.json()["detail"]
    assert (await client.get(f"{one}/members")).json()["items"][0]["user_id"] == dev_id


# ── a group the identity provider pushes (the service an edition calls) ──


async def test_upsert_external_group_creates_then_replaces_the_group_and_its_members(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import OrgGroup, OrgGroupMember
    from choregos_api.db.session import session_scope
    from choregos_api.errors import ApiError
    from choregos_api.services.org_groups import upsert_external_group
    from sqlalchemy import select

    for email in ("a@varga.dev", "b@varga.dev", "c@varga.dev"):
        await _member(client, email, "developer", [])
    a, b, c = [await _user_id(f"{x}@varga.dev") for x in "abc"]
    org_id = await _org_id()

    await _scim_group([a, b], slug="ops", external_id="okta-42")
    await _scim_group([b, c], slug="operations", external_id="okta-42")
    async with session_scope() as session:
        (group,) = (await session.execute(select(OrgGroup))).scalars().all()
        assert (group.slug, group.source, group.external_id) == ("operations", "scim", "okta-42")
        members = set(
            (
                await session.execute(
                    select(OrgGroupMember.user_id).where(OrgGroupMember.group_id == group.id)
                )
            ).scalars()
        )
        assert members == {b, c}

    assert (await _create(client, "architects")).status_code == 201
    outsider = await _session_user("outsider@elsewhere.dev")
    for slug, users in (("architects", [a]), ("Not A Slug", [a]), ("ops2", [a, outsider])):
        with pytest.raises(ApiError):
            async with session_scope() as session:
                await upsert_external_group(
                    session,
                    org_id,
                    external_id="okta-99",
                    slug=slug,
                    name="x",
                    members_user_ids=users,
                    actor="t",
                )
    async with session_scope() as session:
        architects = (
            await session.execute(select(OrgGroup).where(OrgGroup.slug == "architects"))
        ).scalar_one()
        assert architects.source == "internal", "an internal group is never taken over"
        assert (
            await session.execute(select(OrgGroup).where(OrgGroup.external_id == "okta-99"))
        ).first() is None


async def _session_user(email: str) -> str:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        user = User(email=email, display_name=email)
        session.add(user)
        await session.flush()
        return str(user.id)


# ── what a person carries ──


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


async def test_a_member_of_the_internal_group_answers_a_step_addressed_to_it_without_the_claim(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    item_id = await _waiting_ticket(client, project["id"])
    await _member(client, "cap@varga.dev", "developer", [])  # no group in the identity provider's claim
    refused = await _decide(app, "cap@varga.dev", item_id)
    assert refused.status_code == 403 and GROUP in refused.text, refused.text

    assert (await _create(client)).status_code == 201
    assert (await client.post(f"{BASE}/{GROUP}/members", json={"email": "cap@varga.dev"})).status_code == 201
    answered = await _decide(app, "cap@varga.dev", item_id)
    assert answered.status_code == 202, answered.text
    assert await _audited("workitem.decision.group_override") == [], (
        "a member of the group is not an override"
    )


async def test_the_principal_carries_the_table_groups_namespaced_and_only_while_a_member(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    await _member(client, "cap@varga.dev", "developer", ["from-the-idp"])
    assert (await _create(client)).status_code == 201
    assert (await client.post(f"{BASE}/{GROUP}/members", json={"email": "cap@varga.dev"})).status_code == 201

    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from choregos_api.deps import principal_for_user
    from sqlalchemy import select

    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == "cap@varga.dev"))).scalar_one()
        principal = await principal_for_user(session, user, narrow=False)
    assert principal.groups == ["from-the-idp", f"choregos:{ORG}:{GROUP}"]

    cap_id = await _user_id("cap@varga.dev")
    assert (await client.delete(f"/api/v1/orgs/{ORG}/members/{cap_id}")).status_code == 204
    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == "cap@varga.dev"))).scalar_one()
        principal = await principal_for_user(session, user, narrow=False)
    assert principal.groups == ["from-the-idp"], "out of the organisation, out of its groups"
    assert (await client.get(f"{BASE}/{GROUP}")).json()["member_count"] == 0


async def test_an_internal_group_with_a_member_is_known_and_its_members_are_listed(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.human_groups import groups_known_in, members_of_group

    path = f"/api/v1/projects/{project['id']}/workflows/validate"
    assert [w["code"] for w in (await client.post(path, json={"yaml": WORKFLOW})).json()["warnings"]] == [
        "actor.group_unknown"
    ]
    assert (await _create(client)).status_code == 201
    empty = (await client.post(path, json={"yaml": WORKFLOW})).json()["warnings"]
    assert [w["code"] for w in empty] == ["actor.group_unknown"], "an empty group is carried by nobody"

    await _member(client, "cap@varga.dev", "developer", [])
    assert (await client.post(f"{BASE}/{GROUP}/members", json={"email": "cap@varga.dev"})).status_code == 201
    assert (await client.post(path, json={"yaml": WORKFLOW})).json()["warnings"] == []

    org_id = await _org_id()
    async with session_scope() as session:
        assert GROUP in await groups_known_in(session, org_id)
        found = await members_of_group(session, org_id, ORG, project["id"], GROUP)
    assert [(user.email, decides) for user, decides in found] == [("cap@varga.dev", True)]
