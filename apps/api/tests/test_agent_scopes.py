"""Agent ownership scopes (S39-09, ADR 0061): who may create, change, see and pin which agent.

Common (platform), organisation, project and personal agents, through the API: the refusals carry
their reason, a personal agent of someone else does not exist for anyone but an administrator, and a
personal agent or another project's agent can be neither pinned nor named in a workflow.
"""

from __future__ import annotations

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
VIEWER = "viewer@varga.dev"
OWNER = "owner@varga.dev"


async def test_a_developer_creates_a_personal_agent_but_not_an_organisation_one(
    client: AsyncClient, org: str
) -> None:
    await add_member(client, VIEWER, "viewer")
    await as_user(client, DEV)
    mine = await create_agent(client, "my-helper", scope="personal")
    assert mine.status_code == 201, mine.text
    body = mine.json()
    assert (body["scope"], body["owner"], body["project"]) == ("personal", DEV, None)
    assert body["can"]["publish"] and body["can"]["manage"] and not body["can"]["pin"]
    assert body["cannot_reason"] and "personal agent" in body["cannot_reason"]

    shared = await create_agent(client, "for-everyone")
    assert shared.status_code == 403
    assert "org_admin" in shared.text and "personal agent" in shared.text, "the 403 says what to do instead"

    await as_user(client, VIEWER)
    assert (await create_agent(client, "viewer-agent", scope="personal")).status_code == 403


async def test_a_project_owner_owns_the_agents_of_their_project_only(client: AsyncClient, org: str) -> None:
    await make_project(client, "dev")
    await make_project(client, "ops")
    await add_member(client, OWNER, "project_owner", project="dev")
    await as_user(client, ADMIN)
    assert (await create_agent(client, "ops-helper", scope="project", project="ops")).status_code == 201
    await install_common(client)

    await as_user(client, OWNER)
    created = await create_agent(client, "dev-helper", scope="project", project="dev")
    assert created.status_code == 201, created.text
    assert (created.json()["scope"], created.json()["project"]) == ("project", "dev")
    published = await client.post(
        f"{ORG}/agents/dev-helper/versions", json={**SPEC, "instructions": "Better."}
    )
    assert published.status_code == 201 and published.json()["version"] == 2

    refused_elsewhere = await create_agent(client, "another", scope="project", project="ops")
    assert refused_elsewhere.status_code == 403
    on_ops = await client.post(f"{ORG}/agents/ops-helper/versions", json=SPEC)
    assert on_ops.status_code == 403 and "ops" in on_ops.text
    on_common = await client.post(f"{ORG}/agents/developer/versions", json=SPEC)
    assert on_common.status_code == 403
    assert "common agent" in on_common.text and "fork" in on_common.text
    assert (await client.patch(f"{ORG}/agents/developer", json={"display_name": "x"})).status_code == 403
    assert (
        await client.post(f"{ORG}/agents/developer/lifecycle", json={"to": "deprecated"})
    ).status_code == 403
    assert (await create_agent(client, "no-project", scope="project")).status_code == 422


async def test_an_org_admin_changes_a_common_agent_and_it_is_audited(client: AsyncClient, org: str) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    common = await install_common(client)
    assert common["scope"] == "platform"
    published = await client.post(
        f"{ORG}/agents/developer/versions", json={**SPEC, "instructions": "Changed."}
    )
    assert published.status_code == 201 and published.json()["version"] == 2
    async with session_scope() as session:
        actions = (await session.execute(select(AuditLog.action))).scalars().all()
    assert actions.count("agent.publish") == 1


async def test_a_personal_agent_is_invisible_to_everyone_but_its_owner_and_an_admin(
    client: AsyncClient, org: str
) -> None:
    await add_member(client, OTHER, "developer")
    await as_user(client, DEV)
    assert (await create_agent(client, "secret-helper", scope="personal")).status_code == 201

    await as_user(client, OTHER)
    assert "secret-helper" not in [a["slug"] for a in (await client.get(f"{ORG}/agents")).json()]
    for path in ("", "/versions/1", "/metrics", "/runs"):
        found = await client.get(f"{ORG}/agents/secret-helper{path}")
        assert found.status_code == 404, f"{path or 'agent'}: a 404, not a 403 that would reveal it"
    assert (await client.post(f"{ORG}/agents/secret-helper/versions", json=SPEC)).status_code == 404
    assert (await client.get(f"{ORG}/agents?scope=personal")).json() == []

    await as_user(client, DEV)
    assert [a["slug"] for a in (await client.get(f"{ORG}/agents?scope=personal")).json()] == ["secret-helper"]
    await as_user(client, ADMIN)
    assert "secret-helper" in [a["slug"] for a in (await client.get(f"{ORG}/agents")).json()]


async def test_the_list_filters_by_scope_and_by_project(client: AsyncClient, org: str) -> None:
    await make_project(client, "dev")
    await make_project(client, "ops")
    await install_common(client)
    for slug, extra in (
        ("org-helper", {}),
        ("dev-helper", {"scope": "project", "project": "dev"}),
        ("ops-helper", {"scope": "project", "project": "ops"}),
    ):
        assert (await create_agent(client, slug, **extra)).status_code == 201

    async def slugs(query: str) -> list[str]:
        return [a["slug"] for a in (await client.get(f"{ORG}/agents{query}")).json()]

    assert await slugs("?scope=platform") == ["developer"]
    assert await slugs("?scope=org") == ["org-helper"]
    assert await slugs("?scope=project") == ["dev-helper", "ops-helper"]
    assert await slugs("?project=dev") == ["dev-helper"]
    assert (await client.get(f"{ORG}/agents?project=nope")).status_code == 404


async def test_a_project_member_does_not_see_another_projects_agents(client: AsyncClient, org: str) -> None:
    await make_project(client, "dev")
    await make_project(client, "ops")
    await as_user(client, ADMIN)
    await create_agent(client, "ops-helper", scope="project", project="ops")
    await create_agent(client, "dev-helper", scope="project", project="dev")
    await add_member(client, OWNER, "project_owner", project="dev")
    # a person who belongs to `dev` only: no organisation role
    from choregos_api.db.models import Membership
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete, select

    await as_user(client, OWNER)
    async with session_scope() as session:
        await session.execute(
            delete(Membership).where(
                Membership.project_id.is_(None),
                Membership.user_id.in_(select(Membership.user_id).where(Membership.project_id.is_not(None))),
            )
        )
    seen = [a["slug"] for a in (await client.get(f"{ORG}/agents")).json()]
    assert "dev-helper" in seen and "ops-helper" not in seen
    assert (await client.get(f"{ORG}/agents/ops-helper")).status_code == 404


async def test_a_personal_agent_cannot_be_pinned_and_neither_can_another_projects_agent(
    client: AsyncClient, org: str
) -> None:
    dev = await make_project(client, "dev")
    ops = await make_project(client, "ops")
    await as_user(client, ADMIN)
    assert (await create_agent(client, "my-helper", scope="personal")).status_code == 201
    assert (await create_agent(client, "dev-helper", scope="project", project="dev")).status_code == 201
    assert (await create_agent(client, "org-helper")).status_code == 201

    personal = await client.put(f"/api/v1/projects/{dev['id']}/agents/my-helper", json={"version": 1})
    assert personal.status_code == 422 and "personal agent" in personal.text
    foreign = await client.put(f"/api/v1/projects/{ops['id']}/agents/dev-helper", json={"version": 1})
    assert foreign.status_code == 422 and "dev" in foreign.text
    assert (
        await client.put(f"/api/v1/projects/{dev['id']}/agents/dev-helper", json={"version": 1})
    ).status_code == 200
    assert (
        await client.put(f"/api/v1/projects/{ops['id']}/agents/org-helper", json={"version": 1})
    ).status_code == 200


async def test_the_dto_says_what_the_caller_may_do_and_why_not(client: AsyncClient, org: str) -> None:
    await make_project(client, "dev")
    await add_member(client, OWNER, "project_owner", project="dev")
    await install_common(client)
    await as_user(client, OWNER)
    common = (await client.get(f"{ORG}/agents/developer")).json()
    assert common["scope"] == "platform"
    assert common["can"]["publish"] is False and common["can"]["manage"] is False
    assert common["can"]["fork_scopes"] == ["project", "personal"]
    assert "common agent" in common["cannot_reason"]
    await as_user(client, ADMIN)
    as_admin = (await client.get(f"{ORG}/agents/developer")).json()
    assert as_admin["can"]["publish"] and as_admin["cannot_reason"] is None
    assert as_admin["can"]["fork_scopes"] == ["org", "project", "personal"]


async def test_a_trial_is_open_to_a_developer_with_a_personal_agent_only(
    client: AsyncClient, org: str
) -> None:
    dev = await make_project(client, "dev")
    await add_member(client, DEV, "developer")
    await add_member(client, VIEWER, "viewer")
    await as_user(client, DEV)
    assert (await create_agent(client, "my-helper", scope="personal")).status_code == 201
    trial = {"project": "dev", "spec": SPEC, "input": {"title": "T", "body": "B"}}
    assert (await client.post(f"{ORG}/agent-trials", json={**trial, "agent": "my-helper"})).status_code == 201
    await as_user(client, VIEWER)
    assert (await client.post(f"{ORG}/agent-trials", json=trial)).status_code == 403
    await as_user(client, OTHER)
    assert (await client.post(f"{ORG}/agent-trials", json={**trial, "agent": "my-helper"})).status_code == 404
    assert dev["slug"] == "dev"


async def test_promoting_an_agent_changes_its_scope(client: AsyncClient, org: str) -> None:
    await make_project(client, "dev")
    await make_project(client, "ops")
    await add_member(client, OWNER, "project_owner", project="dev")
    await install_common(client)
    await as_user(client, OWNER)
    assert (await create_agent(client, "my-helper", scope="personal")).status_code == 201
    # the owner of the personal agent AND of the project: a promotion to `dev`
    moved = await client.post(f"{ORG}/agents/my-helper/scope", json={"scope": "project", "project": "dev"})
    assert moved.status_code == 200, moved.text
    assert (moved.json()["scope"], moved.json()["project"]) == ("project", "dev")
    to_ops = await client.post(f"{ORG}/agents/my-helper/scope", json={"scope": "project", "project": "ops"})
    assert to_ops.status_code == 403
    to_org = await client.post(f"{ORG}/agents/my-helper/scope", json={"scope": "org"})
    assert to_org.status_code == 403 and "org_admin" in to_org.text
    assert (await client.post(f"{ORG}/agents/developer/scope", json={"scope": "org"})).status_code == 403

    await as_user(client, ADMIN)
    assert (await client.post(f"{ORG}/agents/my-helper/scope", json={"scope": "org"})).json()[
        "scope"
    ] == "org"
    common = await client.post(f"{ORG}/agents/developer/scope", json={"scope": "org"})
    assert common.status_code == 403 and "never changes scope" in common.text
    assert (await client.post(f"{ORG}/agents/my-helper/scope", json={"scope": "project"})).status_code == 422


async def test_an_organisation_agent_pinned_elsewhere_cannot_move_into_one_project(
    client: AsyncClient, org: str
) -> None:
    dev = await make_project(client, "dev")
    ops = await make_project(client, "ops")
    await as_user(client, ADMIN)
    assert (await create_agent(client, "org-helper")).status_code == 201
    assert (
        await client.put(f"/api/v1/projects/{ops['id']}/agents/org-helper", json={"version": 1})
    ).status_code == 200
    blocked = await client.post(f"{ORG}/agents/org-helper/scope", json={"scope": "project", "project": "dev"})
    assert blocked.status_code == 409 and "ops" in blocked.text
    assert (await client.delete(f"/api/v1/projects/{ops['id']}/agents/org-helper")).status_code == 204
    moved = await client.post(f"{ORG}/agents/org-helper/scope", json={"scope": "project", "project": "dev"})
    assert moved.status_code == 200 and moved.json()["project"] == "dev"
    assert dev["slug"] == "dev"


async def test_a_personal_agent_does_not_show_among_a_skills_users(client: AsyncClient, org: str) -> None:
    from .test_bibliotheque_de_skills import FICHIERS

    await as_user(client, ADMIN)
    assert (await client.post(f"{ORG}/skills", json={"files": FICHIERS})).status_code == 201
    uses = {"skills": [{"slug": "procedure-onboarding"}]}
    await as_user(client, DEV)
    assert (
        await create_agent(client, "my-helper", scope="personal", spec={**SPEC, **uses})
    ).status_code == 201
    await as_user(client, ADMIN)
    assert (await create_agent(client, "org-helper", spec={**SPEC, **uses})).status_code == 201
    skill = (await client.get(f"{ORG}/skills/procedure-onboarding")).json()
    assert skill["used_by"] == ["org-helper@1"]
