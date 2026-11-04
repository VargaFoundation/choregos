"""Members per scope (S30-06, plan item 9).

Reading an organisation's members is for its members — nothing was checked before (P0). A
project's members are read with `?project=`, by that project's members. `org_admin` is a role of
the organisation: a project row cannot carry it, since a project role only adds to the org role.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login

MEMBERS = "/api/v1/orgs/varga/members"


async def test_a_stranger_does_not_read_the_organisation_s_members(client: AsyncClient, admin: str) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(Organization(slug="globex", name="Globex"))
    assert (await client.get(MEMBERS)).status_code == 200
    # An administrator of varga is nobody in globex: its members are not his to read.
    refuse = await client.get("/api/v1/orgs/globex/members")
    assert refuse.status_code == 403, refuse.text


async def test_a_project_member_reads_the_project_s_members_and_not_the_organisation_s(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    for body in (
        {"email": "po@varga.dev", "role": "project_owner", "project_slug": "billing-api"},
        {"email": "dev@varga.dev", "role": "developer", "project_slug": "billing-api"},
        {"email": "orga@varga.dev", "role": "viewer"},
    ):
        assert (await client.post(MEMBERS, json=body)).status_code == 201
    client.cookies.clear()
    await login(client, "po@varga.dev")
    of_the_project = await client.get(MEMBERS, params={"project": "billing-api"})
    assert of_the_project.status_code == 200, of_the_project.text
    read = {(m["email"], m["role"]) for m in of_the_project.json()}
    assert {("po@varga.dev", "project_owner"), ("dev@varga.dev", "developer")} <= read
    assert all(m["project_slug"] == "billing-api" for m in of_the_project.json())
    assert "orga@varga.dev" not in {m["email"] for m in of_the_project.json()}
    # The project owner manages the project's members.
    added = await client.post(
        MEMBERS, json={"email": "rc@varga.dev", "role": "release_captain", "project_slug": "billing-api"}
    )
    assert added.status_code == 201, added.text


async def test_org_admin_is_not_a_project_role(client: AsyncClient, project: dict[str, Any]) -> None:
    refuse = await client.post(
        MEMBERS, json={"email": "x@varga.dev", "role": "org_admin", "project_slug": "billing-api"}
    )
    assert refuse.status_code == 422, refuse.text
    assert (await client.post(MEMBERS, json={"email": "x@varga.dev", "role": "org_admin"})).status_code == 201
